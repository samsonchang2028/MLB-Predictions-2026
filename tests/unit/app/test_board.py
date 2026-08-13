"""Unit tests for the APP-001 daily board data-loading/shaping module.

Exercises field pass-through (no recomputed probability/edge math), matchup
labeling (known + unknown team_id), run_date filtering, the synthetic
pass/play threshold default and override, and deterministic game_pk ordering.
No Streamlit import here -- this module is the testable half of APP-001.
"""

from __future__ import annotations

from app.board import DEFAULT_EDGE_THRESHOLD, available_run_dates, latest_run_date, load_daily_board, load_daily_board_with_diagnostics


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
        "prediction_timestamp": "2024-04-01T15:00:00+00:00",
        "game_start_timestamp": "2024-04-02T02:10:00+00:00",
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


def test_model_side_points_to_home_for_positive_edge_and_away_for_negative_edge():
    positive = _record(1, home_id=119, away_id=118, edge=0.03)
    negative = _record(2, home_id=119, away_id=118, edge=-0.03)
    rows = load_daily_board(_FakeStore([positive, negative]))
    by_pk = {row["game_pk"]: row for row in rows}
    assert by_pk[1]["model_side_detail"] == "LAD (home)"
    assert by_pk[1]["action_label"] == "PLAY LAD"
    assert by_pk[2]["model_side_detail"] == "KC (away)"
    assert by_pk[2]["action_label"] == "PLAY KC"


def test_action_label_pass_when_edge_below_display_threshold():
    [row] = load_daily_board(_FakeStore([_record(1, home_id=119, away_id=118, edge=0.001)]))
    assert row["model_side_detail"] == "LAD (home)"
    assert row["action_label"] == "PASS"


def test_timestamps_display_in_pacific_time_not_raw_utc_date():
    [row] = load_daily_board(_FakeStore([_record(1)]))
    assert row["game_start_pacific"] == "2024-04-01 07:10 PM PDT"
    assert row["odds_snapshot_pacific"] == "2024-04-01 07:00 AM PDT"
    assert row["prediction_timestamp_pacific"] == "2024-04-01 08:00 AM PDT"


def test_available_and_latest_run_dates_for_sidebar_filter():
    store = _FakeStore([
        _record(1, run_date="2026-08-12"),
        _record(2, run_date="2026-08-13"),
    ])
    assert available_run_dates(store) == ["2026-08-12", "2026-08-13"]
    assert latest_run_date(store) == "2026-08-13"


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


def test_record_missing_required_field_does_not_crash_whole_board():
    good = _record(1, edge=0.03)
    bad = _record(2, edge=0.04)
    del bad["edge"]

    rows = load_daily_board(_FakeStore([bad, good]))

    assert [row["game_pk"] for row in rows] == [1]


def test_diagnostics_report_skipped_malformed_records():
    bad = _record(2, edge=0.04)
    del bad["edge"]

    report = load_daily_board_with_diagnostics(_FakeStore([bad]))

    assert report["rows"] == []
    assert report["skipped"] == [
        {
            "position": 0,
            "game_pk": 2,
            "run_date": "2024-04-01",
            "reason": "missing required field(s): edge",
        }
    ]


def test_non_numeric_probability_record_is_skipped_with_reason():
    bad = _record(2, edge=0.04)
    bad["model_probability"] = "0.55"

    report = load_daily_board_with_diagnostics(_FakeStore([bad]))

    assert report["rows"] == []
    assert report["skipped"][0]["reason"] == "model_probability must be numeric"
