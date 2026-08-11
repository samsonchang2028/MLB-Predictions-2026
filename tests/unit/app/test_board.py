"""Unit tests for the APP-001 daily board data-loading/shaping module.

Exercises field pass-through (no recomputed probability/edge math), matchup
labeling (known + unknown team_id), run_date filtering, the synthetic
pass/play threshold default and override, and deterministic game_pk ordering.
No Streamlit import here -- this module is the testable half of APP-001.
"""

from __future__ import annotations

import pytest

from app.board import DEFAULT_EDGE_THRESHOLD, load_daily_board


class _FakeStore:
    """Minimal fake exposing the only method load_daily_board relies on."""

    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _record(game_pk, *, home_id=147, away_id=111, edge=0.05, run_date="2024-04-01"):
    return {
        "game_pk": game_pk,
        "model_probability": 0.55,
        "market_probability": 0.55 - edge,
        "edge": edge,
        "odds_snapshot_timestamp": "2024-04-01T14:00:00+00:00",
        "model_version": "v1",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "run_date": run_date,
    }


def test_passes_through_probability_and_edge_verbatim():
    record = _record(1, edge=0.031)
    [row] = load_daily_board(_FakeStore([record]))
    assert row["model_probability"] == record["model_probability"]
    assert row["market_probability"] == record["market_probability"]
    assert row["edge"] == record["edge"]
    assert row["odds_snapshot_timestamp"] == record["odds_snapshot_timestamp"]
    assert row["model_version"] == record["model_version"]


def test_matchup_uses_known_abbreviations_and_falls_back_for_unknown():
    known = _record(1, home_id=147, away_id=111)  # NYY / BOS
    unknown = _record(2, home_id=999, away_id=147)
    rows = load_daily_board(_FakeStore([known, unknown]))
    assert rows[0]["matchup"] == "BOS @ NYY"
    assert rows[1]["matchup"] == "NYY @ Team 999"


def test_run_date_filters_to_one_slate():
    rows = load_daily_board(
        _FakeStore([_record(1, run_date="2024-04-01"), _record(2, run_date="2024-04-02")]),
        run_date="2024-04-02",
    )
    assert [r["game_pk"] for r in rows] == [2]


def test_default_threshold_marks_play_when_edge_meets_it():
    at_threshold = _record(1, edge=DEFAULT_EDGE_THRESHOLD)
    below_threshold = _record(2, edge=DEFAULT_EDGE_THRESHOLD - 0.001)
    negative_edge_over_threshold = _record(3, edge=-(DEFAULT_EDGE_THRESHOLD + 0.01))
    rows = load_daily_board(
        _FakeStore([at_threshold, below_threshold, negative_edge_over_threshold])
    )
    by_pk = {r["game_pk"]: r["play"] for r in rows}
    assert by_pk[1] is True
    assert by_pk[2] is False
    assert by_pk[3] is True  # abs(edge) drives the indicator, not sign


def test_custom_edge_threshold_overrides_default():
    record = _record(1, edge=0.1)
    rows = load_daily_board(_FakeStore([record]), edge_threshold=0.5)
    assert rows[0]["play"] is False


def test_rows_sorted_by_game_pk_regardless_of_store_order():
    rows = load_daily_board(_FakeStore([_record(3), _record(1), _record(2)]))
    assert [r["game_pk"] for r in rows] == [1, 2, 3]


def test_multiple_predictions_for_same_game_pk_are_all_shown():
    # Legitimate PIPE-001 scenario: an odds-refresh re-snapshot writes a second,
    # distinct record for the same game_pk. The board must not silently collapse
    # or overwrite -- both should be visible.
    first = _record(7, edge=0.03)
    second = dict(first, edge=0.05, odds_snapshot_timestamp="2024-04-01T15:00:00+00:00")
    rows = load_daily_board(_FakeStore([first, second]))
    assert len(rows) == 2
    assert sorted(r["edge"] for r in rows) == [0.03, 0.05]


def test_edge_is_displayed_verbatim_even_when_internally_inconsistent():
    # Board must never recompute edge from model/market probability -- it is
    # MARKET-001's job. An edge that does not equal model - market must still
    # pass through unchanged, proving no recomputation happens here.
    record = _record(1, edge=0.05)
    record["model_probability"] = 0.55
    record["market_probability"] = 0.50  # 0.55 - 0.50 = 0.05, but force a mismatch
    record["edge"] = 0.99  # deliberately inconsistent with model/market
    [row] = load_daily_board(_FakeStore([record]))
    assert row["edge"] == 0.99


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP (P2): load_daily_board indexes record['edge'], ['model_probability'], "
        "['market_probability'], ['odds_snapshot_timestamp'], ['model_version'], and "
        "['game_pk'] with plain __getitem__, not .get(). PIPE-001's own writer "
        "(_assert_record_complete) guarantees these fields are non-None on write, but "
        "load_daily_board accepts ANY object exposing .records() and JsonLinesPredictionStore "
        "does no schema validation on read -- a hand-edited, truncated, or stale-schema "
        "JSONL line missing one of these keys crashes the WHOLE board (KeyError, uncaught "
        "in daily_board_page.py) instead of degrading gracefully for just that row. "
        "Remove xfail once load_daily_board either validates/skips incomplete records with "
        "a visible reason, or the crash-on-malformed-store behavior is deliberately documented."
    ),
)
def test_record_missing_required_field_does_not_crash_whole_board():
    record = _record(1, edge=0.03)
    del record["edge"]
    load_daily_board(_FakeStore([record]))
