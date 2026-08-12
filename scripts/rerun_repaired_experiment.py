"""Rerun V1 ML experiments on the repaired 2021-2025 certified build.

Operator entry point. This script rebuilds the FEAT-004 Gold matrix from the
current repaired Silver tables, enforces historical Gold completeness, runs
ML-005/006/007/008 on development folds only, and writes a new report without
overwriting the diagnostic pre-repair artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb  # noqa: E402

from evaluation.calibration import compare_calibration  # noqa: E402
from evaluation.splits import expanding_folds, rolling_folds  # noqa: E402
from experiments.comparison import compare_windows  # noqa: E402
from experiments.expanding import run_expanding  # noqa: E402
from experiments.rolling import run_rolling  # noqa: E402
from features.build import build_feature_matrix  # noqa: E402
from features.bullpen import build_bullpen_features  # noqa: E402
from features.starter import build_starter_features  # noqa: E402
from features.team import build_team_features  # noqa: E402
from models import logistic, random_forest, xgboost_model  # noqa: E402


DEFAULT_CERTIFICATION = (
    Path("state")
    / "data-certifications"
    / "certification-PASS-a910017bac839af5.json"
)
DEFAULT_OUTPUT = Path("reports") / "experiments" / "v1-repaired-a910017bac839af5.json"


def _dict_rows(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = connection.execute(sql)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_inputs(database: str, certification_path: Path) -> dict[str, Any]:
    certification = json.loads(certification_path.read_text(encoding="utf-8"))
    with duckdb.connect(database, read_only=True) as connection:
        games = _dict_rows(
            connection,
            """
            SELECT game_pk, season, game_type, game_date, home_team_id,
                   away_team_id, game_number
            FROM silver.games
            WHERE season IN ('2021', '2022', '2023', '2024', '2025')
            ORDER BY game_pk
            """,
        )
        team_stats = _dict_rows(
            connection,
            """
            SELECT t.game_pk, t.team_id, t.side, t.score, t.is_winner,
                   t.game_date, g.season
            FROM silver.team_game_statistics t
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
              AND g.game_type = 'R'
            ORDER BY t.game_pk, t.team_id
            """,
        )
        appearances = _dict_rows(
            connection,
            """
            SELECT p.game_pk, p.team_id, p.side, g.game_date, g.game_type,
                   g.game_number, p.pitcher_id, p.appearance_order,
                   p.is_actual_starter, p.outs_recorded, p.batters_faced,
                   p.pitches_thrown, p.earned_runs, p.hits_allowed, p.walks,
                   p.strikeouts, p.home_runs_allowed
            FROM silver.pitcher_appearances p
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
            ORDER BY p.game_pk, p.team_id, p.appearance_order
            """,
        )
        starters = _dict_rows(
            connection,
            """
            SELECT ps.game_pk, ps.team_id, ps.side, ps.actual_pitcher_id,
                   ps.probable_pitcher_id
            FROM silver.pitcher_starters ps
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
            ORDER BY ps.game_pk, ps.team_id
            """,
        )
    return {
        "certification": certification,
        "games": games,
        "team_stats": team_stats,
        "appearances": appearances,
        "starters": starters,
    }


def _build_matrix(inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    team = build_team_features(inputs["team_stats"])
    starter = build_starter_features(
        inputs["appearances"], inputs["games"], inputs["starters"]
    )
    bullpen = build_bullpen_features(inputs["appearances"])
    matrix = build_feature_matrix(
        inputs["games"],
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        results=inputs["team_stats"],
        certification=inputs["certification"],
        completeness_mode="historical",
    )
    return matrix, {
        "games": len(inputs["games"]),
        "team_game_statistics_regular": len(inputs["team_stats"]),
        "pitcher_appearances": len(inputs["appearances"]),
        "pitcher_starters": len(inputs["starters"]),
        "team_features": len(team),
        "starter_features": len(starter),
        "bullpen_features": len(bullpen),
    }


def _calibration_folds(window: str) -> list[Any]:
    if window == "expanding":
        return expanding_folds()
    if window == "rolling_2":
        return rolling_folds(2)
    if window == "rolling_3":
        return rolling_folds(3)
    raise ValueError(f"unknown window {window!r}")


def _model_module(name: str) -> Any:
    return {
        "logistic_regression": logistic,
        "random_forest": random_forest,
        "xgboost": xgboost_model,
    }[name]


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rerun repaired 2021-2025 V1 ML experiments."
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    args = parser.parse_args(argv)

    started = time.time()
    certification_path = Path(args.certification)
    output_path = Path(args.output)

    print(f"[load] database={args.database}", flush=True)
    inputs = _load_inputs(args.database, certification_path)
    print("[gold] building repaired historical matrix", flush=True)
    matrix, input_counts = _build_matrix(inputs)
    print(
        "[gold] "
        f"rows={len(matrix['rows'])} excluded={len(matrix['excluded'])} "
        f"columns={len(matrix['feature_columns'])} "
        f"completeness={matrix['feature_completeness']['status']}",
        flush=True,
    )

    print("[ml-005] expanding windows", flush=True)
    expanding = run_expanding(matrix, random_state=args.random_state)
    print("[ml-006] rolling windows", flush=True)
    rolling = run_rolling(matrix, random_state=args.random_state)
    print("[ml-007] comparison", flush=True)
    comparison = compare_windows(
        expanding=expanding, rolling=rolling, random_state=args.random_state
    )
    best = comparison["best"]
    print(
        "[ml-007] best="
        f"{best['model']} {best['window']} "
        f"log_loss={best['log_loss']:.6f} brier={best['brier']:.6f} "
        f"ece={best['ece']:.6f}",
        flush=True,
    )

    print("[ml-008] calibration on selected model/window", flush=True)
    calibration = compare_calibration(
        _model_module(best["model"]),
        matrix,
        _calibration_folds(best["window"]),
        random_state=args.random_state,
    )

    report = {
        "status": "REPAIRED_EXPERIMENT_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "random_state": args.random_state,
        "n_rows": len(matrix["rows"]),
        "n_feature_columns": len(matrix["feature_columns"]),
        "excluded_games": matrix["excluded"],
        "input_counts": input_counts,
        "gold_feature_completeness": matrix["feature_completeness"],
        "common_test_seasons": comparison["common_test_seasons"],
        "ranking": comparison["ranking"],
        "full": comparison["full"],
        "best": comparison["best"],
        "expanding": expanding,
        "rolling": rolling,
        "calibration": calibration,
        "runtime_seconds": round(time.time() - started, 3),
        "holdout_2026": "not inspected",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {output_path}", flush=True)
    print(f"[done] seconds={report['runtime_seconds']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
