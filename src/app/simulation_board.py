"""APP-010 - data loading/shaping for the Streamlit simulation comparison board.

Reads PIPE-001 ``daily.jsonl`` prediction records and PIPE-007 ``simulation.jsonl``
artifacts, joins them on ``(run_date, game_pk)``, and shapes display rows for
``app.simulation_page``.

No probability or runs math happens here — ``model_probability``,
``market_probability``, and simulation ``p_home_win`` / run summaries are read
verbatim from artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.board import (
    _format_pacific,
    _latest_row_per_game,
    _matchup,
    _parse_datetime,
    _team_label,
    available_run_dates,
    latest_run_date,
)

DISAGREEMENT_THRESHOLD = 0.05
DEFAULT_SIMULATION_PATH = Path("state/predictions/simulation.jsonl")
SIMULATION_REQUIRED_FIELDS = (
    "run_date",
    "game_pk",
    "p_home_win",
    "total_runs_mean",
)


class JsonLinesSimulationStore:
    """Read-only JSON-lines store for PIPE-007 simulation artifacts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: list[dict[str, Any]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                self._records.append(json.loads(line))

    def records(self):
        return self._records


def available_simulation_run_dates(store: Any) -> list[str]:
    """Return sorted run_date values available in a simulation store."""
    dates = {
        str(record.get("run_date"))
        for record in store.records()
        if isinstance(record, Mapping) and record.get("run_date") is not None
    }
    return sorted(dates)


def latest_simulation_run_date(store: Any) -> str | None:
    dates = available_simulation_run_dates(store)
    return dates[-1] if dates else None


def _simulation_by_game(store: Any, run_date: Any) -> dict[Any, dict[str, Any]]:
    """Latest simulation artifact per ``game_pk`` for a slate date."""
    latest: dict[Any, dict[str, Any]] = {}
    for record in store.records():
        if not isinstance(record, Mapping):
            continue
        if str(record.get("run_date")) != str(run_date):
            continue
        game_pk = record.get("game_pk")
        if game_pk is None:
            continue
        current = latest.get(game_pk)
        if current is None:
            latest[game_pk] = dict(record)
            continue
        record_ts = _parse_datetime(record.get("simulation_timestamp"))
        current_ts = _parse_datetime(current.get("simulation_timestamp"))
        if record_ts is not None and current_ts is not None:
            if record_ts >= current_ts:
                latest[game_pk] = dict(record)
        else:
            latest[game_pk] = dict(record)
    return latest


def _daily_rows_by_game(daily_store: Any, run_date: Any) -> dict[Any, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in daily_store.records():
        if not isinstance(record, Mapping):
            continue
        if run_date is not None and record.get("run_date") != run_date:
            continue
        if record.get("game_pk") is None:
            continue
        if record.get("model_probability") is None or record.get("market_probability") is None:
            continue
        home_team_id = record.get("home_team_id")
        away_team_id = record.get("away_team_id")
        rows.append(
            {
                "game_pk": record["game_pk"],
                "run_date": record.get("run_date"),
                "matchup": _matchup(away_team_id, home_team_id),
                "home_team": _team_label(home_team_id),
                "away_team": _team_label(away_team_id),
                "p_home_xgb": float(record["model_probability"]),
                "p_home_market": float(record["market_probability"]),
                "model_version": record.get("model_version"),
                "game_start_pacific": _format_pacific(record.get("game_start_timestamp")),
                "prediction_timestamp_pacific": _format_pacific(record.get("prediction_timestamp")),
                "prediction_timestamp": _parse_datetime(record.get("prediction_timestamp")),
            }
        )
    latest = _latest_row_per_game(rows)
    return {row["game_pk"]: row for row in latest}


def load_simulation_board(
    daily_store: Any,
    simulation_store: Any,
    *,
    run_date: Any = None,
) -> list[dict[str, Any]]:
    """Shape joined daily + simulation artifacts into display rows."""
    return load_simulation_board_with_diagnostics(
        daily_store,
        simulation_store,
        run_date=run_date,
    )["rows"]


def load_simulation_board_with_diagnostics(
    daily_store: Any,
    simulation_store: Any,
    *,
    run_date: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return display rows plus skipped malformed simulation records."""
    if run_date is None:
        run_date = latest_run_date(daily_store) or latest_simulation_run_date(simulation_store)

    daily_by_game = _daily_rows_by_game(daily_store, run_date)
    simulation_by_game = _simulation_by_game(simulation_store, run_date)

    skipped: list[dict[str, Any]] = []
    for position, record in enumerate(simulation_store.records()):
        if str(record.get("run_date")) != str(run_date):
            continue
        problem = _simulation_record_problem(record)
        if problem is not None:
            skipped.append(
                {
                    "position": position,
                    "game_pk": record.get("game_pk") if isinstance(record, Mapping) else None,
                    "run_date": record.get("run_date") if isinstance(record, Mapping) else None,
                    "reason": problem,
                }
            )

    rows: list[dict[str, Any]] = []
    game_pks = sorted(set(daily_by_game) | set(simulation_by_game))
    for game_pk in game_pks:
        daily = daily_by_game.get(game_pk)
        sim = simulation_by_game.get(game_pk)
        if daily is None and sim is None:
            continue
        if sim is not None and _simulation_record_problem(sim) is not None:
            continue

        p_home_xgb = daily["p_home_xgb"] if daily else None
        p_home_market = daily["p_home_market"] if daily else None
        p_home_sim = float(sim["p_home_win"]) if sim is not None else None
        disagreement = (
            abs(p_home_xgb - p_home_sim) > DISAGREEMENT_THRESHOLD
            if p_home_xgb is not None and p_home_sim is not None
            else False
        )

        rows.append(
            {
                "game_pk": game_pk,
                "run_date": run_date,
                "matchup": daily["matchup"] if daily else _matchup(
                    sim.get("away_team_id"), sim.get("home_team_id")
                ),
                "home_team": daily["home_team"] if daily else _team_label(sim.get("home_team_id")),
                "away_team": daily["away_team"] if daily else _team_label(sim.get("away_team_id")),
                "p_home_xgb": p_home_xgb,
                "p_home_sim": p_home_sim,
                "p_home_market": p_home_market,
                "disagreement": disagreement,
                "total_runs_mean": sim.get("total_runs_mean") if sim else None,
                "total_runs_median": sim.get("total_runs_median") if sim else None,
                "home_runs_mean": sim.get("home_runs_mean") if sim else None,
                "away_runs_mean": sim.get("away_runs_mean") if sim else None,
                "n_trials": sim.get("n_trials") if sim else None,
                "totals_line": sim.get("totals_line") if sim else None,
                "p_over": sim.get("p_over") if sim else None,
                "p_under": sim.get("p_under") if sim else None,
                "total_runs_quantiles": sim.get("total_runs_quantiles") if sim else None,
                "total_runs_histogram": sim.get("total_runs_histogram") if sim else None,
                "simulation_model_version": sim.get("model_version") if sim else None,
                "simulation_build_id": sim.get("build_id") if sim else None,
                "simulation_timestamp_pacific": _format_pacific(sim.get("simulation_timestamp"))
                if sim
                else None,
                "game_start_pacific": daily["game_start_pacific"] if daily else None,
                "prediction_timestamp_pacific": daily["prediction_timestamp_pacific"]
                if daily
                else None,
                "xgb_model_version": daily["model_version"] if daily else None,
            }
        )

    rows.sort(key=lambda row: row["game_pk"])
    return {"rows": rows, "skipped": skipped}


def slate_probability_chart_frame(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Build grouped-bar chart input keyed by matchup label."""
    frame: dict[str, dict[str, float]] = {}
    for row in rows:
        matchup = str(row["matchup"])
        values: dict[str, float] = {}
        if row.get("p_home_xgb") is not None:
            values["XGBoost"] = float(row["p_home_xgb"])
        if row.get("p_home_sim") is not None:
            values["Simulation"] = float(row["p_home_sim"])
        if row.get("p_home_market") is not None:
            values["Market"] = float(row["p_home_market"])
        if values:
            frame[matchup] = values
    return frame


def total_runs_distribution_frame(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Shape optional histogram or quantile artifacts for Streamlit charts."""
    histogram = row.get("total_runs_histogram")
    if isinstance(histogram, Mapping):
        counts = histogram.get("counts")
        bin_edges = histogram.get("bin_edges")
        if isinstance(counts, list) and isinstance(bin_edges, list) and len(bin_edges) >= 2:
            labels = [
                f"{bin_edges[index]:g}-{bin_edges[index + 1]:g}"
                for index in range(len(bin_edges) - 1)
            ]
            return {"kind": "histogram", "index": labels, "values": {"Trials": counts}}

    quantiles = row.get("total_runs_quantiles")
    if isinstance(quantiles, Mapping) and quantiles:
        ordered = sorted(
            ((str(key), float(value)) for key, value in quantiles.items()),
            key=lambda item: float(item[0].rstrip("%")) if item[0].rstrip("%").replace(".", "", 1).isdigit() else item[0],
        )
        return {
            "kind": "quantiles",
            "index": [label for label, _value in ordered],
            "values": {"Total runs": [value for _label, value in ordered]},
        }

    return None


def _simulation_record_problem(record: Any) -> str | None:
    if not isinstance(record, Mapping):
        return "record is not an object"
    missing = [field for field in SIMULATION_REQUIRED_FIELDS if record.get(field) is None]
    if missing:
        return "missing required field(s): " + ", ".join(missing)
    for field in ("p_home_win", "total_runs_mean"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{field} must be numeric"
    return None
