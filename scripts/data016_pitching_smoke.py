"""DATA-016 live smoke check (opt-in; network; NOT part of the default suite).

Fetches a small set of REAL completed games across MORE THAN ONE season/date
using the repo's production schedule/detail ingestion and production
``GAME_DETAIL_FIELDS`` projection. It normalizes the fetched payloads to a
temporary Silver build, reports required-stat population, and confirms that
starters/relievers remain identifiable. This is the gate the Orchestrator runs
BEFORE launching the multi-hour 2021-2025 re-ingest, so a projection regression
can never silently ship 100%-NULL pitching stats again.

This file lives under ``scripts/`` (not ``tests/``), so pytest never collects it
and it is excluded from the default suite.

How to run (from the repository root / worktree)::

    & 'C:\\Users\\sfkim\\OneDrive\\Desktop\\sideproj\\predictions-1\\.venv\\Scripts\\python.exe' scripts/data016_pitching_smoke.py

Exit code 0 = all sampled games returned real pitching lines; non-zero = a
game came back hollow (fail the backfill gate).
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.mlb.game_detail import (  # noqa: E402
    backfill_game_details,
)
from ingestion.mlb.schedule import ingest_schedule  # noqa: E402
from ingestion.mlb.statsapi_fetchers import (  # noqa: E402
    make_game_detail_fetcher,
    make_schedule_fetcher,
)
from ingestion.odds import ingest_the_odds_api_moneylines  # noqa: E402
from storage import connect_database, storage_paths  # noqa: E402
from transforms import normalize_silver  # noqa: E402

# Multiple dates across multiple seasons (2021-2025 development scope).
SAMPLE_DATES = (
    "2021-04-05",
    "2022-06-10",
    "2023-07-15",
    "2024-08-20",
    "2025-04-01",
)
_SILVER_STATS = (
    "innings_pitched", "outs_recorded", "batters_faced", "pitches_thrown",
    "earned_runs", "hits_allowed", "runs_allowed", "walks", "strikeouts",
    "home_runs_allowed",
)


def _first_final_game_pk(schedule: dict) -> int | None:
    for date in schedule.get("dates", []):
        for game in date.get("games", []):
            if game.get("status", {}).get("abstractGameState") == "Final":
                return int(game["gamePk"])
    return None


def main() -> int:
    schedule_fetch = make_schedule_fetcher()
    detail_fetch = make_game_detail_fetcher()

    with TemporaryDirectory(prefix="data016-smoke-") as storage_root:
        game_pks: list[int] = []
        for date in SAMPLE_DATES:
            payload = schedule_fetch(
                {"sportId": 1, "startDate": date, "endDate": date}
            )
            game_pk = _first_final_game_pk(_decode(payload))
            if game_pk is None:
                print(f"[skip] {date}: no completed game")
                continue
            ingest_schedule(
                storage_root,
                lambda _request, response=payload: response,
                start_date=date,
                end_date=date,
            )
            game_pks.append(game_pk)

        if len(game_pks) < 2:
            raise AssertionError(
                f"smoke check found only {len(game_pks)} completed game(s) across <2 dates"
            )

        result = backfill_game_details(
            storage_root,
            detail_fetch,
            game_pks=game_pks,
            run_id="DATA-016-real-smoke",
            build_id="DATA-016-real-smoke",
        )
        if result["fetched"] != len(game_pks) or result["failed"] or result["missing"]:
            raise AssertionError(
                f"real smoke backfill did not fetch every game: {result}"
            )

        database = storage_paths(storage_root)["database"]
        with connect_database(database) as connection:
            # Silver normalization owns both baseball and odds contracts. The
            # smoke has no odds input, but initializes its empty Bronze source
            # through the production ingestion API so the rebuild is real.
            ingest_the_odds_api_moneylines(connection, [])
            counts = normalize_silver(connection)
            rows = connection.execute(
                f"""SELECT {', '.join(f'count({column})' for column in _SILVER_STATS)},
                           count(*),
                           count(*) FILTER (WHERE is_actual_starter),
                           count(*) FILTER (WHERE NOT is_actual_starter)
                    FROM silver.pitcher_appearances
                    WHERE game_pk IN ({', '.join('?' for _ in game_pks)})""",
                game_pks,
            ).fetchone()
            starters_by_game = connection.execute(
                """SELECT game_pk,
                          count(*) FILTER (WHERE is_actual_starter) AS starters,
                          count(*) FILTER (WHERE NOT is_actual_starter) AS relievers
                   FROM silver.pitcher_appearances
                   GROUP BY game_pk ORDER BY game_pk"""
            ).fetchall()

        total = rows[len(_SILVER_STATS)]
        if total == 0:
            raise AssertionError("real smoke produced zero Silver pitcher appearances")
        print(
            f"[ok]   backfill: {result['fetched']} fetched, "
            f"{total} pitcher appearances"
        )
        print("\nRequired pitcher-stat population:")
        for column, populated in zip(_SILVER_STATS, rows[: len(_SILVER_STATS)]):
            rate = 100.0 * populated / total
            print(f"    {column:20s} {populated:3d}/{total} ({rate:6.2f}%)")
            if populated != total:
                raise AssertionError(
                    f"{column} populated for only {populated}/{total} smoke rows"
                )

        starter_count = rows[-2]
        reliever_count = rows[-1]
        if starter_count != 2 * len(game_pks) or reliever_count == 0:
            raise AssertionError(
                f"starter/reliever identification failed: "
                f"{starter_count} starter(s), {reliever_count} reliever(s)"
            )
        if any(starters != 2 for _, starters, _ in starters_by_game):
            raise AssertionError(
                f"expected two actual starters per game: {starters_by_game}"
            )

        print(
            f"\nStarter/reliever identification: {starter_count} starters, "
            f"{reliever_count} relievers across {len(game_pks)} games"
        )
        print(f"Silver rows: {counts}")
        print(
            f"\nPASS: {len(game_pks)} completed games across "
            f"{len(game_pks)} dates/seasons passed Bronze -> Silver smoke."
        )
    return 0


def _decode(payload: bytes) -> dict:
    import json

    return json.loads(payload.decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
