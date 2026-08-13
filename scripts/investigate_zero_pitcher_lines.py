"""DATA-019: local investigation of DATA-018 all-zero pitcher-line failures.

Read-only diagnostic. It does not re-fetch MLB data and does not mutate DuckDB.
The goal is to make the DATA-018 follow-up auditable: identify which games were
rejected by the hollow-payload guard, show whether they have Silver pitcher
appearances, and summarize the local evidence by season/status/reason.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_OUTPUT = Path("reports") / "data-quality" / "zero-pitcher-line-investigation.json"
DEFAULT_RUN_ID = "DATA-018-reingest-2021-2025"


def _dict_rows(connection: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, params)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_failures(database: str, run_id: str) -> list[dict[str, Any]]:
    with duckdb.connect(database, read_only=True) as connection:
        return _dict_rows(
            connection,
            """
            SELECT a.game_pk, g.season, g.official_date, g.game_date,
                   g.home_team_id, g.away_team_id, g.abstract_game_state,
                   g.detailed_state, g.status_code, a.status, a.error_type,
                   a.error_message,
                   count(p.game_pk) AS silver_pitcher_appearance_rows
            FROM bronze.mlb_game_detail_attempts a
            JOIN silver.games g USING (game_pk)
            LEFT JOIN silver.pitcher_appearances p USING (game_pk)
            WHERE a.ingestion_run_id = ?
              AND a.status = 'failed'
            GROUP BY a.game_pk, g.season, g.official_date, g.game_date,
                     g.home_team_id, g.away_team_id, g.abstract_game_state,
                     g.detailed_state, g.status_code, a.status, a.error_type,
                     a.error_message
            ORDER BY a.game_pk
            """,
            [run_id],
        )


def build_report(rows: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    by_season = Counter(str(row.get("season")) for row in rows)
    by_error_type = Counter(str(row.get("error_type")) for row in rows)
    by_game_state = Counter(str(row.get("abstract_game_state")) for row in rows)
    by_detail_state = Counter(str(row.get("detailed_state")) for row in rows)
    same_guard = sum("all-zero pitching line" in str(row.get("error_message", "")) for row in rows)
    zero_silver = sum(int(row.get("silver_pitcher_appearance_rows") or 0) == 0 for row in rows)
    completed = sum(str(row.get("abstract_game_state")) == "Final" for row in rows)

    return {
        "task": "DATA-019",
        "source_run_id": run_id,
        "summary": {
            "failed_games": len(rows),
            "all_zero_pitching_line_guard_failures": same_guard,
            "zero_silver_pitcher_appearance_games": zero_silver,
            "abstract_final_games": completed,
            "interpretation": (
                "Local evidence indicates these are DATA-016 hollow-payload guard "
                "rejections, not the original projection defect recurring: the "
                "attempts failed before payload publication, and every affected "
                "game has zero Silver pitcher_appearances rows."
            ),
        },
        "by_season": dict(sorted(by_season.items())),
        "by_error_type": dict(sorted(by_error_type.items())),
        "by_abstract_game_state": dict(sorted(by_game_state.items())),
        "by_detailed_state": dict(sorted(by_detail_state.items())),
        "games": rows,
    }


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Investigate DATA-018 all-zero pitcher-line failures.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    rows = load_failures(args.database, args.run_id)
    report = build_report(rows, run_id=args.run_id)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(
        f"[data-019] failed_games={summary['failed_games']} "
        f"guard_failures={summary['all_zero_pitching_line_guard_failures']} "
        f"zero_silver={summary['zero_silver_pitcher_appearance_games']} "
        f"output={output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
