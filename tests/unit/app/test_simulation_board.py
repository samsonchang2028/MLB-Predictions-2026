"""Unit tests for the APP-010 simulation board data-loading/shaping module."""

from __future__ import annotations

import json
from pathlib import Path

from app.simulation_board import (
    DISAGREEMENT_THRESHOLD,
    JsonLinesSimulationStore,
    available_simulation_run_dates,
    latest_simulation_run_date,
    load_simulation_board,
    load_simulation_board_with_diagnostics,
    slate_probability_chart_frame,
    total_runs_distribution_frame,
)


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _daily_record(
    game_pk: int,
    *,
    model_probability: float = 0.55,
    market_probability: float = 0.52,
    run_date: str = "2026-08-13",
    home_id: int = 119,
    away_id: int = 111,
):
    return {
        "game_pk": game_pk,
        "model_probability": model_probability,
        "market_probability": market_probability,
        "edge": model_probability - market_probability,
        "odds_snapshot_timestamp": "2026-08-13T14:00:00+00:00",
        "prediction_timestamp": "2026-08-13T15:00:00+00:00",
        "game_start_timestamp": "2026-08-14T02:10:00+00:00",
        "model_version": "xgb-v1",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "run_date": run_date,
    }


def _simulation_record(
    game_pk: int,
    *,
    p_home_win: float = 0.53,
    run_date: str = "2026-08-13",
    home_id: int = 119,
    away_id: int = 111,
):
    return {
        "run_date": run_date,
        "game_pk": game_pk,
        "p_home_win": p_home_win,
        "home_runs_mean": 4.5,
        "away_runs_mean": 4.2,
        "total_runs_mean": 8.7,
        "total_runs_median": 9.0,
        "n_trials": 10000,
        "model_version": "sim-game-level-v1",
        "build_id": "gold-build-1",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "simulation_timestamp": "2026-08-13T16:00:00+00:00",
    }


def test_joins_daily_and_simulation_probabilities_verbatim():
    rows = load_simulation_board(
        _FakeStore([_daily_record(1, model_probability=0.58, market_probability=0.54)]),
        _FakeStore([_simulation_record(1, p_home_win=0.49)]),
        run_date="2026-08-13",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["p_home_xgb"] == 0.58
    assert row["p_home_market"] == 0.54
    assert row["p_home_sim"] == 0.49
    assert row["matchup"] == "BOS @ LAD"
    assert row["total_runs_mean"] == 8.7


def test_disagreement_flag_when_xgb_and_sim_differ_by_more_than_threshold():
    disagree = load_simulation_board(
        _FakeStore([_daily_record(1, model_probability=0.60)]),
        _FakeStore([_simulation_record(1, p_home_win=0.53)]),
        run_date="2026-08-13",
    )[0]
    agree = load_simulation_board(
        _FakeStore([_daily_record(2, model_probability=0.54)]),
        _FakeStore([_simulation_record(2, p_home_win=0.53)]),
        run_date="2026-08-13",
    )[0]

    assert disagree["disagreement"] is True
    assert abs(disagree["p_home_xgb"] - disagree["p_home_sim"]) > DISAGREEMENT_THRESHOLD
    assert agree["disagreement"] is False


def test_pacific_timestamps_are_formatted_for_display():
    [row] = load_simulation_board(
        _FakeStore([_daily_record(1)]),
        _FakeStore([_simulation_record(1)]),
        run_date="2026-08-13",
    )

    assert row["game_start_pacific"] == "2026-08-13 19:10 PDT"
    assert row["prediction_timestamp_pacific"] == "2026-08-13 08:00 PDT"
    assert row["simulation_timestamp_pacific"] == "2026-08-13 09:00 PDT"


def test_run_date_filter_limits_joined_rows():
    rows = load_simulation_board(
        _FakeStore(
            [
                _daily_record(1, run_date="2026-08-12"),
                _daily_record(2, run_date="2026-08-13"),
            ]
        ),
        _FakeStore(
            [
                _simulation_record(1, run_date="2026-08-12"),
                _simulation_record(2, run_date="2026-08-13"),
            ]
        ),
        run_date="2026-08-13",
    )

    assert [row["game_pk"] for row in rows] == [2]


def test_available_and_latest_simulation_run_dates():
    store = _FakeStore(
        [
            _simulation_record(1, run_date="2026-08-12"),
            _simulation_record(2, run_date="2026-08-13"),
        ]
    )

    assert available_simulation_run_dates(store) == ["2026-08-12", "2026-08-13"]
    assert latest_simulation_run_date(store) == "2026-08-13"


def test_malformed_simulation_record_is_reported_in_diagnostics():
    bad = _simulation_record(1)
    del bad["p_home_win"]

    report = load_simulation_board_with_diagnostics(
        _FakeStore([_daily_record(1)]),
        _FakeStore([bad]),
        run_date="2026-08-13",
    )

    assert report["rows"] == []
    assert report["skipped"][0]["reason"] == "missing required field(s): p_home_win"


def test_slate_probability_chart_frame_groups_three_sources():
    rows = load_simulation_board(
        _FakeStore([_daily_record(1, model_probability=0.58, market_probability=0.54)]),
        _FakeStore([_simulation_record(1, p_home_win=0.49)]),
        run_date="2026-08-13",
    )

    frame = slate_probability_chart_frame(rows)

    assert frame["BOS @ LAD"] == {
        "XGBoost": 0.58,
        "Simulation": 0.49,
        "Market": 0.54,
    }


def test_total_runs_distribution_frame_reads_histogram_bins():
    row = {
        "total_runs_histogram": {
            "bin_edges": [6, 8, 10, 12],
            "counts": [100, 500, 200],
        }
    }

    frame = total_runs_distribution_frame(row)

    assert frame is not None
    assert frame["kind"] == "histogram"
    assert frame["index"] == ["6-8", "8-10", "10-12"]
    assert frame["values"]["Trials"] == [100, 500, 200]


def test_total_runs_distribution_frame_reads_quantiles():
    row = {
        "total_runs_quantiles": {
            "0.25": 7.0,
            "0.5": 9.0,
            "0.75": 10.5,
        }
    }

    frame = total_runs_distribution_frame(row)

    assert frame is not None
    assert frame["kind"] == "quantiles"
    assert frame["values"]["Total runs"] == [7.0, 9.0, 10.5]


def test_json_lines_simulation_store_reads_fixture_file(tmp_path: Path):
    path = tmp_path / "simulation.jsonl"
    path.write_text(
        json.dumps(_simulation_record(823915)) + "\n",
        encoding="utf-8",
    )

    store = JsonLinesSimulationStore(path)

    assert len(list(store.records())) == 1
    assert store.records()[0]["game_pk"] == 823915
