"""Unit tests for APP-013 signal dashboard helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.board import DEFAULT_EDGE_THRESHOLD
from app.signal_dashboard import (
    build_signal_dashboard,
    derive_model_side,
    derive_risk_flags,
    derive_selected_side_edge,
    derive_selected_side_market_probability,
    derive_selected_side_probability,
    derive_signal_label,
    format_edge_pp,
    prepare_daily_signal_table,
    prepare_edge_buckets,
)


def _board_row(*, edge: float = 0.05, game_pk: int = 1) -> dict:
    return {
        "game_pk": game_pk,
        "matchup": "BOS @ NYY",
        "game_start_pacific": "2026-08-14 19:05 PDT",
        "model_probability": 0.55,
        "market_probability": 0.50,
        "edge": edge,
        "model_side": "NYY",
        "pick": "NYY",
        "recommendation": "PLAY NYY" if abs(edge) >= DEFAULT_EDGE_THRESHOLD else "PASS",
        "play": abs(edge) >= DEFAULT_EDGE_THRESHOLD,
        "model_version": "v1",
        "game_start_timestamp": "2026-08-15T02:05:00+00:00",
        "prediction_timestamp": "2026-08-14T20:00:00+00:00",
        "odds_snapshot_timestamp": "2026-08-14T19:55:00+00:00",
        "run_date": "2026-08-14",
        "home_team": "NYY",
        "away_team": "BOS",
    }


def _raw_record(*, edge: float = 0.05, game_pk: int = 1) -> dict:
    return {
        "game_pk": game_pk,
        "run_date": "2026-08-14",
        "edge": edge,
        "model_probability": 0.55,
        "market_probability": 0.50,
        "prediction_timestamp": "2026-08-14T20:00:00+00:00",
        "odds_snapshot_timestamp": "2026-08-14T19:55:00+00:00",
        "game_start_timestamp": "2026-08-15T02:05:00+00:00",
        "model_version": "v1",
        "build_id": "abc123",
        "home_american": -120,
        "away_american": 110,
        "source": "draftkings",
    }


def test_format_edge_pp_uses_percentage_points():
    assert format_edge_pp(0.042) == "+4.2 pp"
    assert format_edge_pp(-0.061) == "-6.1 pp"


def test_home_selected_side_transformation():
    row = _board_row(edge=0.05)
    assert derive_model_side(row) == "HOME"
    assert derive_selected_side_probability(row) == pytest.approx(0.55)
    assert derive_selected_side_market_probability(row) == pytest.approx(0.50)
    assert derive_selected_side_edge(row) == pytest.approx(0.05)


def test_away_selected_side_transformation():
    row = _board_row(edge=-0.053)
    assert derive_model_side(row) == "AWAY"
    assert derive_selected_side_probability(row) == pytest.approx(0.45)
    assert derive_selected_side_market_probability(row) == pytest.approx(0.50)
    assert derive_selected_side_edge(row) == pytest.approx(0.053)


def test_signal_label_mapping():
    assert derive_signal_label(_board_row(edge=0.005)) == "NO EDGE"
    assert derive_signal_label(_board_row(edge=0.05)) == "VALUE ON HOME"
    assert derive_signal_label(_board_row(edge=-0.05)) == "VALUE ON AWAY"
    assert derive_signal_label(_board_row(edge=0.10)) == "REVIEW LARGE EDGE"


def test_risk_flags_for_stale_odds_and_missing_fields():
    now = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)
    row = _raw_record()
    row["odds_snapshot_timestamp"] = "2026-08-14T10:00:00+00:00"
    row["home_american"] = None
    flags = derive_risk_flags(row, now=now)
    assert "STALE_ODDS" in flags
    assert "MISSING_ODDS" in flags


def test_risk_flags_game_starting_soon():
    now = datetime(2026, 8, 15, 1, 45, tzinfo=timezone.utc)
    flags = derive_risk_flags(_raw_record(), now=now)
    assert "GAME_STARTING_SOON" in flags


def test_prepare_daily_signal_table_ranks_by_absolute_edge():
    rows = prepare_daily_signal_table(
        [_board_row(edge=0.01, game_pk=1), _board_row(edge=0.08, game_pk=2)],
        raw_records_by_game={1: _raw_record(edge=0.01, game_pk=1), 2: _raw_record(edge=0.08, game_pk=2)},
        features_path=None,
    )
    assert [row["game_pk"] for row in rows] == [2, 1]
    assert rows[0]["edge_pp_display"] == "+8.0 pp"


def test_prepare_edge_bucket_boundaries():
    buckets = prepare_edge_buckets(
        [_board_row(edge=0.005), _board_row(edge=0.015, game_pk=2), _board_row(edge=0.09, game_pk=3)]
    )
    by_bucket = {row["bucket"]: row["count"] for row in buckets}
    assert by_bucket["|edge| < 1 pp"] == 1
    assert by_bucket["1–2 pp"] == 1
    assert by_bucket["8+ pp"] == 1


def test_build_signal_dashboard_handles_missing_artifacts(tmp_path):
    paths = tmp_path
    dashboard = build_signal_dashboard(
        __import__("app.homepage", fromlist=["ArtifactPaths"]).ArtifactPaths(
            predictions=paths / "missing.jsonl",
            journal=paths / "missing-journal.jsonl",
            skipped=paths / "missing-skipped.jsonl",
            holdout_report=paths / "missing-holdout.json",
            diagnostics_report=paths / "missing-diagnostics.json",
        )
    )
    assert dashboard["signal_table"] == []
    assert dashboard["freshness_warnings"]
