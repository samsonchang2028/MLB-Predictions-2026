"""Unit tests for APP-016 CLV monitor helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.clv_monitor import (
    build_clv_dashboard,
    format_strict_clv_rows,
    read_jsonl,
    summarize_odds_closes,
)


def _prediction(
    game_pk: int,
    *,
    edge: float,
    offset_hours: int = 0,
    home_american: int = -110,
    away_american: int = 100,
    run_date: str = "2026-08-01",
) -> dict:
    model_probability = 0.5 + edge / 2
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
        "source": "the_odds_api:draftkings",
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
) -> dict:
    pred_ts = datetime.fromisoformat(prediction["prediction_timestamp"])
    return {
        "game_pk": prediction["game_pk"],
        "run_date": prediction["run_date"],
        "bookmaker": "draftkings",
        "home_american": home_american if home_american is not None else prediction["home_american"],
        "away_american": away_american if away_american is not None else prediction["away_american"],
        "snapshot_timestamp": (pred_ts + timedelta(minutes=snapshot_offset_minutes)).isoformat(),
        "source": "the_odds_api",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_summarize_odds_closes_live_and_backfill_counts() -> None:
    summary = summarize_odds_closes(
        [
            {"capture_method": "live_pregame", "snapshot_timestamp": "2026-08-01T19:00:00+00:00"},
            {"capture_method": "live_pregame", "snapshot_timestamp": "2026-08-02T19:00:00+00:00"},
            {"capture_method": "backfill_odds_books", "snapshot_timestamp": "2026-08-01T18:00:00+00:00"},
            {"capture_method": "manual_import", "snapshot_timestamp": "2026-08-03T19:00:00+00:00"},
        ]
    )
    assert summary["total_rows"] == 4
    assert summary["live_pregame_rows"] == 2
    assert summary["backfill_rows"] == 1
    assert summary["other_rows"] == 1
    assert summary["latest_snapshot_timestamp"] == "2026-08-03T19:00:00+00:00"


def test_summarize_odds_closes_empty() -> None:
    summary = summarize_odds_closes([])
    assert summary["total_rows"] == 0
    assert summary["live_pregame_rows"] == 0
    assert summary["backfill_rows"] == 0
    assert summary["latest_snapshot_timestamp"] is None


def test_format_strict_clv_rows_display_shape() -> None:
    population = [
        {
            "strict_clv_valid": True,
            "run_date": "2026-08-02",
            "game_pk": 102,
            "game_start_timestamp": "2026-08-02T20:00:00+00:00",
            "edge_selected_side": "HOME",
            "clv": 0.025,
            "closing_odds_source": "odds_closes",
            "prediction_market_p_selected": 0.45,
            "closing_market_p_selected": 0.475,
            "journal_correct": True,
        },
        {
            "strict_clv_valid": True,
            "run_date": "2026-08-01",
            "game_pk": 101,
            "game_start_timestamp": "2026-08-01T20:00:00+00:00",
            "edge_selected_side": "AWAY",
            "clv": -0.01,
            "closing_odds_source": "odds_books",
            "prediction_market_p_selected": 0.40,
            "closing_market_p_selected": 0.39,
            "journal_correct": False,
        },
        {
            "strict_clv_valid": False,
            "run_date": "2026-08-03",
            "game_pk": 103,
            "clv": 0.05,
        },
    ]
    rows = format_strict_clv_rows(population, limit=10)
    assert len(rows) == 2
    assert rows[0]["game_pk"] == 102
    assert rows[0]["CLV"] == "2.50 pp"
    assert rows[0]["Result"] == "Win"
    assert rows[1]["Result"] == "Loss"
    assert rows[1]["Close source"] == "odds_books"


def test_build_clv_dashboard_ok_with_fixtures(tmp_path: Path) -> None:
    pred = _prediction(400, edge=0.03)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    pred_path = tmp_path / "daily.jsonl"
    journal_path = tmp_path / "journal.jsonl"
    odds_path = tmp_path / "odds_books.jsonl"
    closes_path = tmp_path / "odds_closes.jsonl"
    _write_jsonl(pred_path, [pred])
    _write_jsonl(journal_path, [_journal(pred, actual_home_win=True)])
    _write_jsonl(odds_path, odds)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    _write_jsonl(
        closes_path,
        [
            {
                "run_date": pred["run_date"],
                "game_pk": pred["game_pk"],
                "bookmaker": "draftkings",
                "prediction_timestamp": pred_ts.isoformat(),
                "home_american": -130,
                "away_american": 115,
                "snapshot_timestamp": (pred_ts + timedelta(minutes=45)).isoformat(),
                "source": "the_odds_api",
                "capture_method": "live_pregame",
            }
        ],
    )

    dashboard = build_clv_dashboard(
        predictions_path=pred_path,
        journal_path=journal_path,
        odds_books_path=odds_path,
        odds_closes_path=closes_path,
    )

    assert dashboard["status"] == "ok"
    assert dashboard["metrics"]["strict_clv_n"] == 1
    assert dashboard["metrics"]["strict_mean_clv_pp"] is not None
    assert dashboard["capture_summary"]["live_pregame_rows"] == 1
    assert dashboard["capture_summary"]["backfill_rows"] == 0
    assert len(dashboard["strict_clv_rows"]) == 1
    assert dashboard["verdict"] is not None


def test_build_clv_dashboard_no_predictions(tmp_path: Path) -> None:
    pred_path = tmp_path / "daily.jsonl"
    pred_path.write_text("", encoding="utf-8")
    journal_path = tmp_path / "journal.jsonl"
    odds_path = tmp_path / "odds_books.jsonl"
    closes_path = tmp_path / "odds_closes.jsonl"
    _write_jsonl(journal_path, [])
    _write_jsonl(odds_path, [])
    _write_jsonl(
        closes_path,
        [{"capture_method": "backfill_odds_books", "snapshot_timestamp": "2026-08-01T19:00:00+00:00"}],
    )

    dashboard = build_clv_dashboard(
        predictions_path=pred_path,
        journal_path=journal_path,
        odds_books_path=odds_path,
        odds_closes_path=closes_path,
    )

    assert dashboard["status"] == "no_predictions"
    assert dashboard["capture_summary"]["total_rows"] == 1
    assert dashboard["capture_summary"]["backfill_rows"] == 1


def test_build_clv_dashboard_missing_odds_closes_graceful(tmp_path: Path) -> None:
    pred = _prediction(401, edge=0.04)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    pred_path = tmp_path / "daily.jsonl"
    journal_path = tmp_path / "journal.jsonl"
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(pred_path, [pred])
    _write_jsonl(journal_path, [_journal(pred, actual_home_win=False)])
    _write_jsonl(odds_path, odds)

    dashboard = build_clv_dashboard(
        predictions_path=pred_path,
        journal_path=journal_path,
        odds_books_path=odds_path,
        odds_closes_path=None,
    )

    assert dashboard["status"] == "ok"
    assert dashboard["capture_summary"]["total_rows"] == 0
    assert dashboard["metrics"]["strict_clv_n"] == 1


def test_build_clv_dashboard_integrity_error(tmp_path: Path) -> None:
    pred = _prediction(402, edge=0.03)
    journal = _journal(pred, actual_home_win=True)
    journal["predicted_home_win"] = not journal["predicted_home_win"]
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    pred_path = tmp_path / "daily.jsonl"
    journal_path = tmp_path / "journal.jsonl"
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(pred_path, [pred])
    _write_jsonl(journal_path, [journal])
    _write_jsonl(odds_path, odds)

    dashboard = build_clv_dashboard(
        predictions_path=pred_path,
        journal_path=journal_path,
        odds_books_path=odds_path,
    )

    assert dashboard["status"] == "integrity_error"
    assert "journal predicted_home_win mismatches" in dashboard["error"]


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "missing.jsonl") == []
