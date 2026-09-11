"""Unit tests for market CLV helpers and evaluation."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from market.clv import (
    EDGE_BUCKET_SPECS_PP,
    close_after_first_pitch,
    close_at_or_before_prediction,
    compute_clv,
    edge_bucket_label,
    interpret_clv_verdict,
    selected_side_market_probability,
    strict_close_valid,
)
from market.clv_evaluation import (
    bookmaker_from_source,
    build_clv_report,
    clv_metrics,
    index_odds_books,
    load_clv_population,
    outcome_metrics,
    render_markdown_report,
)


def _prediction(
    game_pk: int,
    *,
    edge: float,
    model_probability: float | None = None,
    market_probability: float | None = None,
    offset_hours: int = 0,
    home_american: int = -110,
    away_american: int = 100,
    source: str = "the_odds_api:draftkings",
    run_date: str = "2026-08-01",
) -> dict:
    if model_probability is None:
        model_probability = 0.5 + edge / 2
    if market_probability is None:
        market_probability = model_probability - edge
    base = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc) + timedelta(hours=offset_hours)
    return {
        "game_pk": game_pk,
        "run_date": run_date,
        "model_probability": model_probability,
        "market_probability": market_probability,
        "edge": edge,
        "home_american": home_american,
        "away_american": away_american,
        "odds_snapshot_timestamp": (base - timedelta(minutes=30)).isoformat(),
        "prediction_timestamp": base.isoformat(),
        "game_start_timestamp": (base + timedelta(hours=2)).isoformat(),
        "model_version": "test-model",
        "build_id": "test-build",
        "source": source,
    }


def _journal(prediction: dict, *, actual_home_win: bool) -> dict:
    picked_home = float(prediction["edge"]) >= 0.0
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "model_version": prediction["model_version"],
        "enrichment_timestamp": "2026-08-02T12:00:00+00:00",
        "actual_home_win": actual_home_win,
        "predicted_home_win": picked_home,
        "correct": picked_home == actual_home_win,
    }


def _odds_row(
    prediction: dict,
    *,
    snapshot_offset_minutes: int = 60,
    home_american: int | None = None,
    away_american: int | None = None,
    bookmaker: str = "draftkings",
) -> dict:
    pred_ts = datetime.fromisoformat(prediction["prediction_timestamp"])
    return {
        "game_pk": prediction["game_pk"],
        "run_date": prediction["run_date"],
        "bookmaker": bookmaker,
        "home_american": home_american if home_american is not None else prediction["home_american"],
        "away_american": away_american if away_american is not None else prediction["away_american"],
        "snapshot_timestamp": (pred_ts + timedelta(minutes=snapshot_offset_minutes)).isoformat(),
        "source": "the_odds_api",
    }


def test_selected_side_market_probability_home_and_away() -> None:
    row = {"market_probability": 0.60}
    assert selected_side_market_probability(row, side="HOME") == pytest.approx(0.60)
    assert selected_side_market_probability(row, side="AWAY") == pytest.approx(0.40)


def test_compute_clv_selected_side() -> None:
    clv = compute_clv(prediction_market_p_selected=0.45, closing_market_p_selected=0.50)
    assert clv == pytest.approx(0.05)


def test_strict_close_timestamp_rules() -> None:
    pred = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)
    start = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
    close_valid = datetime(2026, 8, 1, 19, 0, tzinfo=timezone.utc)
    close_before_pred = datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)
    close_after_start = datetime(2026, 8, 1, 21, 0, tzinfo=timezone.utc)

    assert strict_close_valid(pred, close_valid, start) is True
    assert strict_close_valid(pred, close_before_pred, start) is False
    assert strict_close_valid(pred, close_after_start, start) is False
    assert close_at_or_before_prediction(pred, close_before_pred) is True
    assert close_after_first_pitch(close_after_start, start) is True


def test_edge_bucket_boundaries_at_2_4_6_8pp() -> None:
    assert edge_bucket_label(0.019, EDGE_BUCKET_SPECS_PP) == "0-2pp"
    assert edge_bucket_label(0.02, EDGE_BUCKET_SPECS_PP) == "2-4pp"
    assert edge_bucket_label(0.039, EDGE_BUCKET_SPECS_PP) == "2-4pp"
    assert edge_bucket_label(0.04, EDGE_BUCKET_SPECS_PP) == "4-6pp"
    assert edge_bucket_label(0.06, EDGE_BUCKET_SPECS_PP) == "6-8pp"
    assert edge_bucket_label(0.08, EDGE_BUCKET_SPECS_PP) == "8pp+"


def test_load_clv_population_dedups_latest_per_game() -> None:
    older = _prediction(100, edge=0.03, offset_hours=0)
    newer = _prediction(100, edge=0.05, offset_hours=24)
    other = _prediction(101, edge=0.04, offset_hours=48)
    odds = [
        _odds_row(older, snapshot_offset_minutes=30),
        _odds_row(newer, snapshot_offset_minutes=30),
        _odds_row(other, snapshot_offset_minutes=30),
    ]
    rows, meta = load_clv_population([older, newer, other], [], odds)
    assert meta["n_latest_per_game"] == 2
    by_pk = {row["game_pk"]: row for row in rows}
    assert by_pk[100]["edge"] == pytest.approx(0.05)


def test_missing_close_rejected_from_strict_population() -> None:
    pred = _prediction(200, edge=0.03)
    rows, _ = load_clv_population([pred], [], [])
    assert rows[0]["strict_clv_valid"] is False
    assert rows[0]["strict_clv_rejection_reason"] == "missing_bookmaker_row"
    assert rows[0]["clv"] is None


def test_unresolved_game_has_clv_without_outcome() -> None:
    pred = _prediction(201, edge=0.04, home_american=-150, away_american=130)
    odds = [_odds_row(pred, snapshot_offset_minutes=45, home_american=-140, away_american=120)]
    rows, _ = load_clv_population([pred], [], odds)
    assert rows[0]["strict_clv_valid"] is True
    assert rows[0]["clv"] is not None
    outcome = outcome_metrics(rows)
    assert outcome["n_resolved"] == 0
    assert outcome["pending"] == 1
    metrics = clv_metrics(rows, strict=True)
    assert metrics["clv_n"] == 1
    assert metrics["outcome_n"] == 0


def test_roi_and_clv_denominators_separated() -> None:
    resolved = _prediction(300, edge=0.05, home_american=-200, away_american=170)
    pending = _prediction(301, edge=0.04)
    odds = [
        _odds_row(resolved, snapshot_offset_minutes=30, home_american=-180, away_american=160),
        _odds_row(pending, snapshot_offset_minutes=30),
    ]
    journal = [_journal(resolved, actual_home_win=True)]
    rows, _ = load_clv_population([resolved, pending], journal, odds)
    strict = clv_metrics(rows, strict=True)
    outcome = outcome_metrics(rows)
    assert strict["clv_n"] == 2
    assert strict["outcome_n"] == 1
    assert outcome["n_resolved"] == 1
    assert outcome["roi"] == pytest.approx(0.5)


def test_bookmaker_from_source_and_index() -> None:
    assert bookmaker_from_source("the_odds_api:draftkings") == "draftkings"
    assert bookmaker_from_source(None) == "draftkings"
    indexed = index_odds_books(
        [{"game_pk": 1, "run_date": "2026-08-01", "bookmaker": "draftkings"}]
    )
    assert (1, "2026-08-01", "draftkings") in indexed


def test_interpret_clv_verdict_insufficient_data() -> None:
    verdict = interpret_clv_verdict(mean_clv=0.01, roi=0.05, clv_n=8)
    assert verdict["case"] == 4
    assert verdict["verdict"] == "CLV DATA INSUFFICIENT"


def test_build_clv_report_no_network() -> None:
    pred = _prediction(400, edge=0.03)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    report = build_clv_report(
        daily_records=[pred],
        journal_records=[_journal(pred, actual_home_win=True)],
        odds_records=odds,
        run_id="test",
        input_paths={"predictions": "daily.jsonl", "journal": "journal.jsonl", "odds_books": "odds.jsonl"},
    )
    assert report["status"] == "MARKET_008_CLV_VALIDATION"
    assert "aggregate" in report
    assert "slices" in report


def test_build_clv_report_verdict_uses_strict_outcome_roi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verdict ROI must come from strict-close cohort, not full population."""
    captured: dict[str, object] = {}
    original = interpret_clv_verdict

    def _spy(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return original(**{k: v for k, v in kwargs.items()})  # type: ignore[arg-type]

    monkeypatch.setattr("market.clv_evaluation.interpret_clv_verdict", _spy)

    winner = _prediction(500, edge=0.05, home_american=-200, away_american=170)
    loser = _prediction(501, edge=0.04, home_american=-150, away_american=130)
    odds = [
        _odds_row(winner, snapshot_offset_minutes=30, home_american=-180, away_american=160),
        _odds_row(loser, snapshot_offset_minutes=-30, home_american=-140, away_american=120),
    ]
    journal = [
        _journal(winner, actual_home_win=True),
        _journal(loser, actual_home_win=False),
    ]
    report = build_clv_report(
        daily_records=[winner, loser],
        journal_records=journal,
        odds_records=odds,
        run_id="strict-roi",
        input_paths={"predictions": "d.jsonl", "journal": "j.jsonl", "odds_books": "o.jsonl"},
    )
    strict_roi = report["aggregate"]["strict_close"]["outcome_roi"]
    full_roi = report["aggregate"]["outcome_all_latest_per_game"]["roi"]
    assert strict_roi == pytest.approx(0.5)
    assert full_roi == pytest.approx(-0.25)
    assert captured["roi"] == strict_roi
    assert captured["roi"] != full_roi


@pytest.mark.parametrize(
    ("mean_clv", "roi", "expected_case", "expected_verdict"),
    [
        (0.01, 0.05, 1, "POSITIVE CLV WITH POSITIVE ROI"),
        (0.01, -0.05, 2, "POSITIVE CLV WITH NEGATIVE ROI"),
        (-0.01, 0.05, 3, "NEGATIVE OR FLAT CLV"),
        (0.0, 0.05, 3, "NEGATIVE OR FLAT CLV"),
    ],
)
def test_interpret_clv_verdict_cases_1_2_3(
    mean_clv: float,
    roi: float,
    expected_case: int,
    expected_verdict: str,
) -> None:
    verdict = interpret_clv_verdict(mean_clv=mean_clv, roi=roi, clv_n=30)
    assert verdict["case"] == expected_case
    assert verdict["verdict"] == expected_verdict


def test_journal_side_mismatch_raises_value_error() -> None:
    pred = _prediction(600, edge=0.03)
    journal = _journal(pred, actual_home_win=True)
    journal["predicted_home_win"] = not journal["predicted_home_win"]
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    with pytest.raises(ValueError, match="journal predicted_home_win mismatches"):
        build_clv_report(
            daily_records=[pred],
            journal_records=[journal],
            odds_records=odds,
            run_id="mismatch",
            input_paths={"predictions": "d.jsonl", "journal": "j.jsonl", "odds_books": "o.jsonl"},
        )


def test_prediction_at_or_after_first_pitch_raises_value_error() -> None:
    pred = _prediction(601, edge=0.03)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    pred["prediction_timestamp"] = (pred_ts + timedelta(hours=3)).isoformat()
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    with pytest.raises(ValueError, match="predictions at or after first pitch"):
        build_clv_report(
            daily_records=[pred],
            journal_records=[],
            odds_records=odds,
            run_id="late-pred",
            input_paths={"predictions": "d.jsonl", "journal": "j.jsonl", "odds_books": "o.jsonl"},
        )


@pytest.mark.parametrize(
    ("snapshot_offset_minutes", "expected_reason"),
    [
        (-30, "close_at_or_before_prediction"),
        (180, "close_after_first_pitch"),
    ],
)
def test_strict_rejection_timestamp_paths(
    snapshot_offset_minutes: int,
    expected_reason: str,
) -> None:
    pred = _prediction(700 + snapshot_offset_minutes, edge=0.03)
    odds = [_odds_row(pred, snapshot_offset_minutes=snapshot_offset_minutes)]
    rows, _ = load_clv_population([pred], [], odds)
    assert rows[0]["strict_clv_valid"] is False
    assert rows[0]["strict_clv_rejection_reason"] == expected_reason
    assert rows[0]["clv"] is None


def test_strict_rejection_missing_close_timestamp() -> None:
    pred = _prediction(710, edge=0.03)
    odds = {
        "game_pk": pred["game_pk"],
        "run_date": pred["run_date"],
        "bookmaker": "draftkings",
        "home_american": pred["home_american"],
        "away_american": pred["away_american"],
        "source": "the_odds_api",
    }
    rows, _ = load_clv_population([pred], [], [odds])
    assert rows[0]["strict_clv_rejection_reason"] == "missing_close_timestamp"


def test_cli_exits_nonzero_on_integrity_failure(tmp_path: Path) -> None:
    pred = _prediction(800, edge=0.03)
    journal = _journal(pred, actual_home_win=True)
    journal["predicted_home_win"] = not journal["predicted_home_win"]
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]

    pred_path = tmp_path / "daily.jsonl"
    journal_path = tmp_path / "journal.jsonl"
    odds_path = tmp_path / "odds.jsonl"
    pred_path.write_text(json.dumps(pred) + "\n", encoding="utf-8")
    journal_path.write_text(json.dumps(journal) + "\n", encoding="utf-8")
    odds_path.write_text(json.dumps(odds) + "\n", encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "market_clv_study.py"),
            "--predictions",
            str(pred_path),
            "--journal",
            str(journal_path),
            "--odds-books",
            str(odds_path),
            "--run-id",
            "cli-fail",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "integrity failure" in result.stderr


def test_load_clv_population_prefers_odds_closes_rejection_over_odds_books() -> None:
    pred = _prediction(951, edge=0.03)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    odds_books = [_odds_row(pred, snapshot_offset_minutes=180)]
    odds_closes = [
        {
            "run_date": pred["run_date"],
            "game_pk": pred["game_pk"],
            "bookmaker": "draftkings",
            "prediction_timestamp": pred_ts.isoformat(),
            "home_american": -130,
            "away_american": 115,
            "snapshot_timestamp": (pred_ts - timedelta(minutes=30)).isoformat(),
            "source": "the_odds_api",
            "capture_method": "backfill_odds_books",
        }
    ]
    rows, _ = load_clv_population([pred], [], odds_books, odds_closes)
    assert rows[0]["strict_clv_valid"] is False
    assert rows[0]["strict_clv_rejection_reason"] == "odds_closes_close_at_or_before_prediction"
    assert rows[0]["clv"] is None


def test_load_clv_population_prefers_odds_closes() -> None:
    pred = _prediction(950, edge=0.03)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    odds_books = [_odds_row(pred, snapshot_offset_minutes=30, home_american=-140, away_american=120)]
    odds_closes = [
        {
            "run_date": pred["run_date"],
            "game_pk": pred["game_pk"],
            "bookmaker": "draftkings",
            "prediction_timestamp": pred_ts.isoformat(),
            "home_american": -130,
            "away_american": 115,
            "snapshot_timestamp": (pred_ts + timedelta(minutes=45)).isoformat(),
            "source": "the_odds_api",
            "capture_method": "backfill_odds_books",
        }
    ]
    rows, _ = load_clv_population([pred], [], odds_books, odds_closes)
    assert rows[0]["strict_clv_valid"] is True
    assert rows[0]["closing_odds_source"] == "odds_closes"
    assert rows[0]["closing_market_p_home"] is not None


def test_load_clv_population_earliest_anchor() -> None:
    older = _prediction(960, edge=0.03, offset_hours=0)
    newer = _prediction(960, edge=0.05, offset_hours=24)
    odds = [
        _odds_row(older, snapshot_offset_minutes=30),
        _odds_row(newer, snapshot_offset_minutes=30),
    ]
    rows, meta = load_clv_population(
        [older, newer],
        [],
        odds,
        prediction_anchor="earliest",
    )
    assert meta["n_latest_per_game"] == 1
    assert rows[0]["edge"] == pytest.approx(0.03)


def test_build_clv_report_documents_prediction_anchor() -> None:
    pred = _prediction(970, edge=0.03)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    report = build_clv_report(
        daily_records=[pred],
        journal_records=[_journal(pred, actual_home_win=True)],
        odds_records=odds,
        odds_closes_records=[],
        run_id="anchor-doc",
        input_paths={"predictions": "d.jsonl", "journal": "j.jsonl", "odds_books": "o.jsonl"},
        prediction_anchor="earliest",
    )
    assert "earliest prediction per game_pk" in report["population"]["prediction_anchor"]
    md = render_markdown_report(report)
    assert "Prediction anchor" in md


def test_render_markdown_includes_slice_tables() -> None:
    pred = _prediction(900, edge=0.03)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    report = build_clv_report(
        daily_records=[pred],
        journal_records=[_journal(pred, actual_home_win=True)],
        odds_records=odds,
        run_id="md-slices",
        input_paths={"predictions": "d.jsonl", "journal": "j.jsonl", "odds_books": "o.jsonl"},
    )
    md = render_markdown_report(report)
    for heading in (
        "Strict-close slices (crossover)",
        "Strict-close slices (model/market agreement)",
        "Strict-close slices (selected side)",
        "Strict-close slices (market favorite role)",
        "Strict-close slices (lead time)",
        "Strict-close slices (bookmaker)",
    ):
        assert heading in md
