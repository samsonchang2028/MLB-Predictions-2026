"""OBS-002 - enrich daily predictions with completed MLB game results.

This is the production wiring around OBS-001's append-only journal. It refreshes
the target day's MLB schedule, normalizes Silver so newly Final games are visible,
then appends result-enrichment rows to ``state/predictions/journal.jsonl``.

It never mutates ``state/predictions/daily.jsonl`` and never creates new
predictions. Result enrichment is post-game observability only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb  # noqa: E402

from ingestion.mlb.schedule import ingest_schedule  # noqa: E402
from ingestion.mlb.statsapi_fetchers import make_schedule_fetcher  # noqa: E402
from observability.journal import (  # noqa: E402
    JsonLinesJournalStore,
    attach_results,
)
from pipelines.daily import JsonLinesPredictionStore  # noqa: E402

DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_PREDICTIONS = Path("state") / "predictions" / "daily.jsonl"
DEFAULT_JOURNAL = Path("state") / "predictions" / "journal.jsonl"


def parse_date(value: str | None) -> date:
    if value is None:
        return datetime.now().date()
    return date.fromisoformat(value)


def parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--enrichment-timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def refresh_schedule_results(database: str | Path, run_date: date) -> dict[str, Any]:
    """Fetch the target day's schedule into Bronze.

    Full ``normalize_silver()`` is intentionally not called here. On the local
    historical database it can take minutes, while OBS-002 only needs the final
    score/winner values carried by the freshly ingested schedule JSON. Result
    loading after a refresh therefore reads the updated Bronze canonical game
    rows directly.
    """
    storage_root = Path(database).parent
    return ingest_schedule(
        storage_root,
        make_schedule_fetcher(),
        start_date=run_date,
        end_date=run_date,
    )


def load_completed_result_rows(
    connection: duckdb.DuckDBPyConnection,
    run_date: date | None = None,
    *,
    game_pks: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    """Return team result rows for completed regular-season games.

    ``run_date`` is used when the real Silver ``games`` table has an
    ``official_date`` column. Unit fixtures may omit it; in that compact shape,
    callers must filter by explicit ``game_pks``.
    """
    game_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info('silver.games')").fetchall()
    }
    params: list[Any] = []
    filters = ["g.game_type = 'R'"]
    if run_date is not None and "official_date" in game_columns:
        filters.append("g.official_date = ?")
        params.append(run_date)
    if game_pks:
        placeholders = ", ".join("?" for _ in game_pks)
        filters.append(f"g.game_pk IN ({placeholders})")
        params.extend(game_pks)
    elif run_date is None or "official_date" not in game_columns:
        return []

    final_filter = "g.abstract_game_state = 'Final'"
    if "coded_game_state" in game_columns:
        final_filter = "(g.abstract_game_state = 'Final' OR g.coded_game_state = 'F')"
    filters.append(final_filter)
    where_clause = " AND ".join(filters)

    cursor = connection.execute(
        f"""
        WITH candidate_games AS (
            SELECT g.game_pk
            FROM silver.games AS g
            JOIN silver.team_game_statistics AS t
              ON t.game_pk = g.game_pk
            WHERE {where_clause}
            GROUP BY g.game_pk
            HAVING count(*) = 2
               AND bool_and(t.score IS NOT NULL)
               AND bool_and(t.is_winner IS NOT NULL)
        )
        SELECT t.game_pk, t.team_id, t.score, t.is_winner
        FROM silver.team_game_statistics AS t
        JOIN candidate_games AS c
          ON c.game_pk = t.game_pk
        ORDER BY t.game_pk, t.team_id
        """,
        params,
    )
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_final_results(database: str | Path, game_pks: Sequence[Any]) -> list[dict[str, Any]]:
    """Load final result rows for explicit game IDs from local Silver."""
    with duckdb.connect(str(database), read_only=True) as connection:
        return load_completed_result_rows(connection, game_pks=game_pks)


def load_completed_result_rows_from_bronze(
    connection: duckdb.DuckDBPyConnection,
    run_date: date,
    *,
    game_pks: Sequence[Any],
) -> list[dict[str, Any]]:
    """Return completed-game result rows from refreshed Bronze schedule JSON."""
    if not game_pks:
        return []
    placeholders = ", ".join("?" for _ in game_pks)
    rows = connection.execute(
        f"""
        SELECT game_pk, home_team_id, away_team_id, abstract_game_state,
               coded_game_state, game_json
        FROM bronze.mlb_games
        WHERE official_date = ?
          AND game_type = 'R'
          AND game_pk IN ({placeholders})
        ORDER BY game_pk
        """,
        [run_date, *game_pks],
    ).fetchall()
    results: list[dict[str, Any]] = []
    for game_pk, home_team_id, away_team_id, abstract_state, coded_state, game_json in rows:
        if abstract_state != "Final" and coded_state != "F":
            continue
        game = json.loads(game_json)
        teams = game.get("teams") if isinstance(game, dict) else None
        if not isinstance(teams, dict):
            continue
        home = teams.get("home")
        away = teams.get("away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        home_score = home.get("score")
        away_score = away.get("score")
        home_winner = home.get("isWinner")
        away_winner = away.get("isWinner")
        if (
            isinstance(home_score, bool)
            or isinstance(away_score, bool)
            or not isinstance(home_score, int)
            or not isinstance(away_score, int)
            or not isinstance(home_winner, bool)
            or not isinstance(away_winner, bool)
        ):
            continue
        results.append(
            {
                "game_pk": game_pk,
                "team_id": home_team_id,
                "score": home_score,
                "is_winner": home_winner,
            }
        )
        results.append(
            {
                "game_pk": game_pk,
                "team_id": away_team_id,
                "score": away_score,
                "is_winner": away_winner,
            }
        )
    return results

def _predictions_for_date(path: Path, run_date: date) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    store = JsonLinesPredictionStore(path)
    run_date_str = str(run_date)
    return [
        record
        for record in store.records()
        if str(record.get("run_date")) == run_date_str
    ]


def enrich_predictions(
    *,
    database: str | Path,
    predictions_path: str | Path,
    journal_path: str | Path,
    run_date: str | date,
    enrichment_timestamp: datetime,
) -> dict[str, Any]:
    """Enrich one slate using existing prediction and Silver result artifacts."""
    parsed_date = date.fromisoformat(run_date) if isinstance(run_date, str) else run_date
    predictions = _predictions_for_date(Path(predictions_path), parsed_date)
    game_pks = sorted({record["game_pk"] for record in predictions})
    with duckdb.connect(str(database), read_only=True) as connection:
        results = load_completed_result_rows(connection, parsed_date, game_pks=game_pks)
    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=enrichment_timestamp,
        store=JsonLinesJournalStore(Path(journal_path)),
    )
    return {
        "predictions": len(predictions),
        "result_rows": len(results),
        "records": len(outcome.records),
        "written": len(outcome.written),
        "skipped": len(outcome.skipped),
        "skip_reasons": _summarize(outcome.skipped),
    }

def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("reason")) for row in rows))


def run(args: argparse.Namespace) -> int:
    run_date = parse_date(args.date)
    enrichment_timestamp = parse_timestamp(args.enrichment_timestamp)
    predictions_path = Path(args.predictions)
    journal_path = Path(args.journal)

    predictions = _predictions_for_date(predictions_path, run_date)
    print(
        f"[load] run_date={run_date} predictions={len(predictions)} "
        f"source={predictions_path}",
        flush=True,
    )
    if not predictions:
        print("[enrich] no predictions for date; nothing to enrich", flush=True)
        return 0

    used_bronze_results = False
    if args.skip_schedule_refresh:
        print("[schedule-refresh] skipped", flush=True)
    else:
        refreshed = refresh_schedule_results(args.database, run_date)
        used_bronze_results = True
        print(
            f"[schedule-refresh] games_seen={refreshed.get('games_seen')} "
            f"payload={refreshed.get('payload_sha256')}",
            flush=True,
        )

    game_pks = sorted({record["game_pk"] for record in predictions})
    with duckdb.connect(str(args.database), read_only=True) as connection:
        if used_bronze_results:
            try:
                results = load_completed_result_rows_from_bronze(
                    connection, run_date, game_pks=game_pks
                )
            except duckdb.CatalogException:
                results = load_completed_result_rows(
                    connection, run_date, game_pks=game_pks
                )
        else:
            results = load_completed_result_rows(connection, run_date, game_pks=game_pks)
    completed_games = len({row["game_pk"] for row in results})
    print(
        f"[results] completed_games={completed_games} team_rows={len(results)}",
        flush=True,
    )

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=enrichment_timestamp,
        store=JsonLinesJournalStore(journal_path),
    )
    print(
        f"[journal] records={len(outcome.records)} written={len(outcome.written)} "
        f"skipped={len(outcome.skipped)} output={journal_path}",
        flush=True,
    )
    print(f"[skips] {json.dumps(_summarize(outcome.skipped), sort_keys=True)}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enrich immutable daily MLB predictions with completed results."
    )
    parser.add_argument("--date", default=None, help="Official MLB date YYYY-MM-DD")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument(
        "--enrichment-timestamp",
        default=None,
        help="ISO timestamp with timezone; default now UTC.",
    )
    parser.add_argument(
        "--skip-schedule-refresh",
        action="store_true",
        help="Use existing Silver result rows without refreshing MLB schedule.",
    )
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as error:  # noqa: BLE001 - command-line operator boundary
        print(f"[error] {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
