"""Unit tests for the APP-001 daily board data-loading/shaping module.

Exercises field pass-through (no recomputed probability/edge math), matchup
labeling (known + unknown team_id), run_date filtering, the synthetic
pass/play threshold default and override, and deterministic game_pk ordering.
No Streamlit import here -- this module is the testable half of APP-001.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    PENDING_STARTER_MESSAGE,
    SKIP_NO_STARTER_ANNOUNCED,
    available_run_dates,
    latest_run_date,
    load_daily_board,
    load_daily_board_with_diagnostics,
    load_starter_pending_games,
)


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
    assert by_pk[1]["pick"] == "LAD"
    assert by_pk[1]["pick_side"] == "home"
    assert by_pk[1]["model_chance"] == positive["model_probability"]
    assert by_pk[1]["market_chance"] == positive["market_probability"]
    assert round(by_pk[1]["difference"], 6) == round(positive["edge"], 6)
    assert by_pk[2]["model_side_detail"] == "KC (away)"
    assert by_pk[2]["action_label"] == "PLAY KC"
    assert by_pk[2]["pick"] == "KC"
    assert by_pk[2]["pick_side"] == "away"
    assert by_pk[2]["model_chance"] == 1.0 - negative["model_probability"]
    assert by_pk[2]["market_chance"] == 1.0 - negative["market_probability"]
    assert round(by_pk[2]["difference"], 6) == round(abs(negative["edge"]), 6)


def test_action_label_pass_when_edge_below_display_threshold():
    [row] = load_daily_board(_FakeStore([_record(1, home_id=119, away_id=118, edge=0.001)]))
    assert row["model_side_detail"] == "LAD (home)"
    assert row["action_label"] == "PASS"
    assert row["recommendation"] == "PASS"
    assert row["play"] is False


def test_timestamps_display_in_pacific_time_not_raw_utc_date():
    [row] = load_daily_board(_FakeStore([_record(1)]))
    assert row["game_start_pacific"] == "2024-04-01 19:10 PDT"
    assert row["odds_snapshot_pacific"] == "2024-04-01 07:00 PDT"
    assert row["prediction_timestamp_pacific"] == "2024-04-01 08:00 PDT"


def test_pacific_timestamps_are_24_hour_so_text_sort_matches_chronological_order():
    # 12-hour "01:05 PM" would sort before "10:35 AM" as plain text; 24-hour
    # "13:05"/"10:35" sorts correctly. Cover an afternoon time whose 12-hour
    # form would have broken a lexicographic sort against a morning time.
    afternoon = _record(1, run_date="2024-04-01")
    afternoon["game_start_timestamp"] = "2024-04-02T00:05:00+00:00"  # 2024-04-01 17:05 PDT
    morning = _record(2, run_date="2024-04-01")
    morning["game_start_timestamp"] = "2024-04-01T17:35:00+00:00"  # 2024-04-01 10:35 PDT

    rows = load_daily_board(_FakeStore([afternoon, morning]))
    by_pk = {row["game_pk"]: row for row in rows}
    assert sorted(row["game_start_pacific"] for row in rows) == [
        by_pk[2]["game_start_pacific"],
        by_pk[1]["game_start_pacific"],
    ]


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


def test_board_shows_latest_prediction_per_game_pk():
    # Scheduled re-runs append a newer prediction_timestamp for the same slate
    # game; the board shows only the most recent snapshot per game_pk.
    first = _record(7, edge=0.03)
    first["prediction_timestamp"] = "2024-04-01T14:00:00+00:00"
    second = dict(first, edge=0.05, prediction_timestamp="2024-04-01T16:00:00+00:00")
    rows = load_daily_board(_FakeStore([first, second]))
    assert len(rows) == 1
    assert rows[0]["edge"] == 0.05


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


def test_load_starter_pending_games_reads_skipped_jsonl(tmp_path: Path):
    skipped_path = tmp_path / "skipped.jsonl"
    skipped_path.write_text(
        json.dumps(
            {
                "run_date": "2026-08-13",
                "game_pk": 823915,
                "reason": SKIP_NO_STARTER_ANNOUNCED,
                "home_team_id": 119,
                "away_team_id": 158,
                "game_start_timestamp": "2026-08-14T02:10:00+00:00",
                "message": PENDING_STARTER_MESSAGE,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = load_starter_pending_games(skipped_path, "2026-08-13")

    assert len(rows) == 1
    assert rows[0]["game_pk"] == 823915
    assert rows[0]["matchup"] == "MIL @ LAD"
    assert rows[0]["message"] == PENDING_STARTER_MESSAGE
    assert rows[0]["game_start_pacific"].startswith("2026-08-13")


def test_board_joins_result_enrichment_by_prediction_key():
    prediction = _record(1, edge=0.03)
    journal = {
        "game_pk": 1,
        "prediction_timestamp": prediction["prediction_timestamp"],
        "model_version": "v1",
        "enrichment_timestamp": "2026-08-14T06:00:00+00:00",
        "actual_home_win": True,
        "predicted_home_win": True,
        "correct": True,
        "home_score": 5,
        "away_score": 3,
    }

    [row] = load_daily_board(_FakeStore([prediction]), journal_store=_FakeStore([journal]))

    assert row["result_status"] == "Final"
    assert row["result_label"] == "Final: Home 5 - Away 3"
    assert row["actual_home_win"] is True
    assert row["correct"] is True


def test_board_leaves_unenriched_predictions_pending():
    [row] = load_daily_board(_FakeStore([_record(1)]), journal_store=_FakeStore([]))

    assert row["result_status"] == "Pending"
    assert row["result_label"] is None
    assert row["correct"] is None
