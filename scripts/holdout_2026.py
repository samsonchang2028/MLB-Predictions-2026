"""ML-010: operator entry point for the untouched 2026 final holdout evaluation.

Loads certified 2021-2026 inputs from DuckDB, builds the Gold historical
feature matrix (identical build path used by the repaired 2021-2025
experiments, extended to include the 2026 season), and calls
``evaluation.holdout.run_holdout_evaluation`` exactly once with the ADR-006
locked V1 methodology. Writes a JSON report with build/certification/model
traceability.

This script is NOT runnable until an operator has ingested and certified the
2026 season under the same DATA-006/DATA-007 rules used for 2021-2025 (see
``scripts/certify_historical.py`` / ``src/pipelines/certify_historical.py``)
and produced a certification artifact covering seasons 2021-2026. There is no
default certification path because that artifact does not exist yet -- pass
``--certification`` explicitly once it does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from evaluation.holdout import (  # noqa: E402
    ADR_006_PATH,
    require_locked_methodology,
    run_holdout_evaluation,
)
from rerun_repaired_experiment import _build_matrix  # noqa: E402

DEFAULT_OUTPUT = Path("reports") / "experiments" / "v1-holdout-2026.json"
SEASONS: tuple[str, ...] = ("2021", "2022", "2023", "2024", "2025", "2026")


def _dict_rows(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = connection.execute(sql)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_inputs(database: str, certification_path: Path) -> dict[str, Any]:
    """Load 2021-2026 Silver inputs. Extends
    ``rerun_repaired_experiment._load_inputs``'s season filter (which
    hardcodes 2021-2025) to include the 2026 holdout season."""
    certification = json.loads(certification_path.read_text(encoding="utf-8"))
    season_list = ", ".join(f"'{season}'" for season in SEASONS)
    with duckdb.connect(database, read_only=True) as connection:
        games = _dict_rows(
            connection,
            f"""
            SELECT game_pk, season, game_type, game_date, home_team_id,
                   away_team_id, game_number
            FROM silver.games
            WHERE season IN ({season_list})
            ORDER BY game_pk
            """,
        )
        team_stats = _dict_rows(
            connection,
            f"""
            SELECT t.game_pk, t.team_id, t.side, t.score, t.is_winner,
                   t.game_date, g.season
            FROM silver.team_game_statistics t
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ({season_list})
              AND g.game_type = 'R'
            ORDER BY t.game_pk, t.team_id
            """,
        )
        appearances = _dict_rows(
            connection,
            f"""
            SELECT p.game_pk, p.team_id, p.side, g.game_date, g.game_type,
                   g.game_number, p.pitcher_id, p.appearance_order,
                   p.is_actual_starter, p.outs_recorded, p.batters_faced,
                   p.pitches_thrown, p.earned_runs, p.hits_allowed, p.walks,
                   p.strikeouts, p.home_runs_allowed
            FROM silver.pitcher_appearances p
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ({season_list})
            ORDER BY p.game_pk, p.team_id, p.appearance_order
            """,
        )
        starters = _dict_rows(
            connection,
            f"""
            SELECT ps.game_pk, ps.team_id, ps.side, ps.actual_pitcher_id,
                   ps.probable_pitcher_id
            FROM silver.pitcher_starters ps
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ({season_list})
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


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the ADR-006 locked V1 methodology on the untouched "
            "2026 final holdout. Run exactly once."
        )
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument(
        "--certification",
        required=True,
        help=(
            "Path to a certification artifact covering seasons 2021-2026 "
            "(state/data-certifications/certification-PASS-<build_id>.json)."
        ),
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    args = parser.parse_args(argv)

    started = time.time()
    certification_path = Path(args.certification)
    output_path = Path(args.output)

    print("[gate] checking ADR-006 methodology lock", flush=True)
    require_locked_methodology(ADR_006_PATH)

    print(f"[load] database={args.database} seasons={SEASONS}", flush=True)
    inputs = _load_inputs(args.database, certification_path)
    print("[gold] building 2021-2026 historical matrix", flush=True)
    matrix, input_counts = _build_matrix(inputs)
    print(
        "[gold] "
        f"rows={len(matrix['rows'])} excluded={len(matrix['excluded'])} "
        f"columns={len(matrix['feature_columns'])} "
        f"completeness={matrix['feature_completeness']['status']}",
        flush=True,
    )

    print("[ml-010] evaluating 2026 final holdout (locked ADR-006 model)", flush=True)
    result = run_holdout_evaluation(matrix, random_state=args.random_state)
    metrics = result["metrics"]
    print(
        "[ml-010] "
        f"log_loss={metrics['log_loss']:.6f} brier={metrics['brier']:.6f} "
        f"ece={metrics['ece']:.6f} n_test={metrics['n_test']}",
        flush=True,
    )

    report = {
        "status": "V1_2026_FINAL_HOLDOUT_EVALUATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "adr": "docs/decisions/ADR-006-v1-methodology-lock.md",
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "git_commit": _git_commit(),
        "random_state": args.random_state,
        "n_rows": len(matrix["rows"]),
        "n_feature_columns": len(matrix["feature_columns"]),
        "excluded_games": matrix["excluded"],
        "input_counts": input_counts,
        "gold_feature_completeness": matrix["feature_completeness"],
        "model": result["model"],
        "locked_params": result["locked_params"],
        "metrics": metrics,
        "predictions": result["predictions"],
        "runtime_seconds": round(time.time() - started, 3),
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
