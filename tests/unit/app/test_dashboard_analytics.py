"""Unit tests for APP-013 dashboard analytics helpers."""

from __future__ import annotations

import math

import pytest

from app.dashboard_analytics import (
    HISTORICAL_EVIDENCE_LABEL,
    PROSPECTIVE_EVIDENCE_LABEL,
    build_betting_results_summary,
    build_edge_buckets,
    build_market_edge_summary,
    build_probability_buckets,
    build_prospective_model_quality,
    compute_probability_metrics,
    is_play,
    model_predicted_home,
    picked_home,
    prediction_key,
    resolved_prediction_rows,
    selected_side_probability,
)


def _prediction(
    game_pk: int,
    *,
    edge: float = 0.05,
    run_date: str = "2026-08-13",
    timestamp: str = "2026-08-13T16:00:00+00:00",
    home_american: int = -110,
    away_american: int = -110,
) -> dict:
    return {
        "game_pk": game_pk,
        "run_date": run_date,
        "edge": edge,
        "model_probability": 0.55,
        "market_probability": 0.50,
        "prediction_timestamp": timestamp,
        "model_version": "v1",
        "home_team_id": 147,
        "away_team_id": 111,
        "home_american": home_american,
        "away_american": away_american,
    }


def _journal_for(prediction: dict, *, correct: bool = True) -> dict:
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "actual_home_win": correct,
        "correct": correct,
        "enrichment_timestamp": "2026-08-14T06:00:00+00:00",
    }


def test_selected_side_probability_transforms_for_away_pick():
    away_pick = _prediction(1, edge=-0.04)
    assert picked_home(away_pick) is False
    assert selected_side_probability(away_pick) == pytest.approx(0.45)


def test_compute_probability_metrics_excludes_empty_input():
    assert compute_probability_metrics([], []) is None


def test_resolved_prediction_rows_keep_latest_snapshot_per_game():
    first = _prediction(1, timestamp="2026-08-13T16:00:00+00:00")
    second = _prediction(1, timestamp="2026-08-13T18:00:00+00:00")
    journal = [_journal_for(first), _journal_for(second, correct=False)]
    rows = resolved_prediction_rows([first, second, first], journal)
    assert len(rows) == 1
    assert prediction_key(rows[0]) == prediction_key(second)
    assert rows[0]["correct"] is False


def test_latest_unresolved_snapshot_does_not_count_stale_resolved_snapshot():
    first = _prediction(
        1,
        run_date="2026-08-13",
        timestamp="2026-08-13T16:00:00+00:00",
    )
    latest = _prediction(
        1,
        run_date="2026-08-14",
        timestamp="2026-08-14T18:00:00+00:00",
    )
    summary = build_prospective_model_quality([first, latest], [_journal_for(first)])

    assert summary["resolved_count"] == 0
    assert summary["pending_count"] == 1


def test_distinct_game_pks_keep_doubleheader_games_separate():
    game_one = _prediction(101, timestamp="2026-08-13T16:00:00+00:00")
    game_two = _prediction(102, timestamp="2026-08-13T20:00:00+00:00")
    rows = resolved_prediction_rows(
        [game_one, game_two],
        [_journal_for(game_one), _journal_for(game_two, correct=False)],
    )

    assert {row["game_pk"] for row in rows} == {101, 102}


def test_model_winner_uses_probability_boundary_not_edge_side():
    home_model_away_pick = {
        **_prediction(1, edge=-0.06),
        "model_probability": 0.54,
    }
    away_model_home_pick = {
        **_prediction(2, edge=0.04),
        "model_probability": 0.46,
    }

    assert model_predicted_home(home_model_away_pick) is True
    assert picked_home(home_model_away_pick) is False
    assert model_predicted_home(away_model_home_pick) is False
    assert picked_home(away_model_home_pick) is True


def test_resolved_prediction_rows_exclude_pending_predictions():
    resolved = _prediction(1)
    pending = _prediction(2)
    rows = resolved_prediction_rows([resolved, pending], [_journal_for(resolved)])
    assert len(rows) == 1
    assert rows[0]["game_pk"] == 1


def test_build_prospective_model_quality_separates_labels_and_counts():
    resolved = _prediction(1)
    pending = _prediction(2)
    summary = build_prospective_model_quality(
        [resolved, pending],
        [_journal_for(resolved)],
    )
    assert summary["evidence_label"] == PROSPECTIVE_EVIDENCE_LABEL
    assert summary["resolved_count"] == 1
    assert summary["pending_count"] == 1
    assert summary["metrics"]["n"] == 1


def test_build_probability_buckets_respect_boundaries_and_zero_sample():
    rows = resolved_prediction_rows(
        [
            _prediction(1, edge=0.03),
            _prediction(2, edge=0.03),
        ],
        [_journal_for(_prediction(1)), _journal_for(_prediction(2), correct=False)],
    )
    rows[0]["model_probability"] = 0.47
    rows[0]["actual_home_win"] = True
    rows[1]["model_probability"] = 0.52
    rows[1]["actual_home_win"] = False
    buckets = build_probability_buckets(rows)
    by_bucket = {row["bucket"]: row for row in buckets}
    assert by_bucket["45–50%"]["n"] == 1
    assert by_bucket["50–55%"]["n"] == 1
    assert by_bucket["65%+"]["n"] == 0
    assert by_bucket["65%+"]["actual_win_rate"] is None


def test_probability_buckets_cover_predictions_below_45_percent():
    prediction = {
        **_prediction(1),
        "model_probability": 0.40,
    }
    rows = resolved_prediction_rows([prediction], [_journal_for(prediction)])

    buckets = build_probability_buckets(rows)

    assert sum(bucket["n"] for bucket in buckets) == 1
    assert {bucket["bucket"]: bucket["n"] for bucket in buckets}["<45%"] == 1


def test_build_edge_buckets_track_play_only_subset():
    play = _prediction(1, edge=0.05)
    pass_row = _prediction(2, edge=0.005)
    resolved = resolved_prediction_rows(
        [play, pass_row],
        [_journal_for(play), _journal_for(pass_row, correct=False)],
    )
    all_buckets = build_edge_buckets(resolved, play_only=False)
    play_buckets = build_edge_buckets(resolved, play_only=True)
    assert sum(row["n"] for row in all_buckets) >= sum(row["n"] for row in play_buckets)
    assert is_play(pass_row) is False


def test_build_betting_results_summary_uses_play_rows_only():
    play = _prediction(1, edge=0.05, home_american=100)
    pass_row = _prediction(2, edge=0.005)
    summary = build_betting_results_summary(
        [play, pass_row],
        [_journal_for(play), _journal_for(pass_row, correct=False)],
    )
    assert summary["play_count"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["units"] == 1.0


def test_betting_results_counts_latest_unresolved_play_as_pending():
    first = _prediction(1, timestamp="2026-08-13T16:00:00+00:00")
    latest = _prediction(1, timestamp="2026-08-13T18:00:00+00:00")
    summary = build_betting_results_summary([first, latest], [_journal_for(first)])

    assert summary["play_count"] == 1
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert summary["pending"] == 1
    assert summary["win_rate"] is None


def test_build_market_edge_summary_does_not_imply_profitability_note():
    board_rows = [
        {
            "game_pk": 1,
            "matchup": "BOS @ NYY",
            "edge": 0.06,
            "model_probability": 0.56,
            "market_probability": 0.50,
            "pick": "NYY",
            "recommendation": "PLAY NYY",
        }
    ]
    summary = build_market_edge_summary(board_rows)
    assert summary["average_edge"] == 0.06
    assert summary["largest_disagreements"][0]["edge"] == 0.06
    assert "profit" in summary["note"].lower()


def test_historical_label_is_not_used_for_prospective_summary():
    summary = build_prospective_model_quality(
        [_prediction(1)],
        [_journal_for(_prediction(1))],
    )
    assert summary["evidence_label"] != HISTORICAL_EVIDENCE_LABEL


def test_compute_probability_metrics_handles_single_class_auc():
    metrics = compute_probability_metrics([True, True], [0.6, 0.7])
    assert metrics is not None
    assert metrics["roc_auc"] is None
    assert math.isfinite(metrics["log_loss"])
