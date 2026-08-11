"""DATA-018 operator entry point: the real 2021-2025 game-detail RE-INGEST.

This is the multi-hour, single-writer, LIVE-NETWORK run that actually replaces
the hollow pitching data left by the pre-DATA-016 build. It is NOT invoked by
any test and must NOT be run automatically -- it requires an explicit
Orchestrator/operator go-ahead (see ``state/CURRENT.md``).

Precondition: ``scripts/data016_pitching_smoke.py`` has already PASSED (a
small multi-game, multi-season real smoke check of the DATA-016 projection
fix). That script is the "smoke before full backfill" gate; this script IS the
full backfill and does not repeat the smoke check.

What this does, per DATA-018:

1. Selects every 2021-2025 ``game_pk`` currently in ``bronze.mlb_games`` (the
   whole build is hollow -- this is not a partial-subset repair).
2. Invalidates the stale hollow ``bronze.mlb_game_detail_payloads`` row for
   each targeted game_pk that has not already been re-fetched under THIS
   run_id (``invalidate_game_detail_payloads``). Raw files on disk are never
   touched; ``bronze.mlb_game_detail_attempts`` is never touched.
3. Re-runs ``backfill_game_details(..., retry_unresolved=True)`` with the real
   MLB-StatsAPI fetcher, processed in batches so progress prints incrementally
   (this runs for hours).
4. Re-normalizes Silver.
5. Re-certifies (``certify_and_write``).

Resumable / restartable (DATA-010 semantics): re-run this script with the SAME
``--run-id`` after an interruption. Step 2 only invalidates game_pks that do
NOT already carry a payload stored under this run_id, so a game_pk this run
already genuinely re-fetched is left alone (never re-invalidated, never
re-fetched); a game_pk not yet reached simply has nothing to invalidate (a
no-op) and is retried by step 3 as usual.

How to run (from the repository root)::

    & 'C:\\Users\\sfkim\\OneDrive\\Desktop\\sideproj\\predictions-1\\.venv\\Scripts\\python.exe' \
        scripts/data018_reingest.py --run-id DATA-018-reingest-2021-2025

Before running: confirm the DATA-016 smoke check passes. After running: check
the printed ``certification_status`` and inspect the written certification
artifact under ``state/data-certifications/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.mlb.game_detail import (  # noqa: E402
    DEFAULT_SEASONS,
    backfill_game_details,
    invalidate_game_detail_payloads,
)
from ingestion.odds import ingest_the_odds_api_moneylines  # noqa: E402
from storage import connect_database, initialize_storage, storage_paths  # noqa: E402
from transforms import normalize_silver  # noqa: E402
from validation import certify_and_write  # noqa: E402

DEFAULT_RUN_ID = "DATA-018-reingest-2021-2025"
DEFAULT_BUILD_ID = "DATA-018"
DEFAULT_BATCH_SIZE = 250
DEFAULT_CERTIFICATIONS_DIR = Path("state") / "data-certifications"


def _target_game_pks(storage_root: str | Path, seasons: tuple[int, ...]) -> list[int]:
    paths = initialize_storage(storage_root)
    placeholders = ", ".join("?" for _ in seasons)
    with connect_database(paths["database"]) as connection:
        return [
            row[0]
            for row in connection.execute(
                f"""SELECT game_pk FROM bronze.mlb_games
                    WHERE try_cast(season AS INTEGER) IN ({placeholders})
                    ORDER BY game_pk""",
                list(seasons),
            ).fetchall()
        ]


def _already_reingested_this_run(
    storage_root: str | Path, game_pks: list[int], run_id: str
) -> set[int]:
    """game_pks that already carry a payload row stored under ``run_id``.

    These must NOT be invalidated again on a restart -- their re-fetched data
    is already the good, current-projection data.
    """
    if not game_pks:
        return set()
    paths = storage_paths(storage_root)
    placeholders = ", ".join("?" for _ in game_pks)
    with connect_database(paths["database"]) as connection:
        return {
            row[0]
            for row in connection.execute(
                f"""SELECT game_pk FROM bronze.mlb_game_detail_payloads
                    WHERE game_pk IN ({placeholders}) AND ingestion_run_id = ?""",
                [*game_pks, run_id],
            ).fetchall()
        }


def _batched(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main(argv: list[str] | None = None) -> int:
    from ingestion.mlb.statsapi_fetchers import make_game_detail_fetcher

    parser = argparse.ArgumentParser(
        description="DATA-018: invalidate + re-ingest the hollow 2021-2025 "
        "game-detail build, then re-normalize and re-certify."
    )
    parser.add_argument("--storage-root", default="data")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--build-id", default=DEFAULT_BUILD_ID)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--min-interval", type=float, default=0.4)
    parser.add_argument(
        "--certifications-dir", default=str(DEFAULT_CERTIFICATIONS_DIR)
    )
    args = parser.parse_args(argv)

    print(f"[targets] selecting 2021-2025 game_pks from bronze.mlb_games")
    targets = _target_game_pks(args.storage_root, DEFAULT_SEASONS)
    print(f"[targets] {len(targets)} game_pks targeted")
    if not targets:
        print("[targets] nothing to do")
        return 0

    already_done = _already_reingested_this_run(args.storage_root, targets, args.run_id)
    to_invalidate = [pk for pk in targets if pk not in already_done]
    print(
        f"[invalidate] {len(already_done)} already re-fetched under run_id="
        f"{args.run_id!r} (skipped); invalidating {len(to_invalidate)} stale rows"
    )
    if to_invalidate:
        deleted = invalidate_game_detail_payloads(args.storage_root, to_invalidate)
        print(f"[invalidate] deleted {deleted} stale payload rows")

    detail_fetch = make_game_detail_fetcher(min_interval=args.min_interval)
    batches = _batched(targets, args.batch_size)
    fetched_total = missing_total = failed_total = skipped_total = 0
    for index, batch in enumerate(batches, start=1):
        result = backfill_game_details(
            args.storage_root,
            detail_fetch,
            game_pks=batch,
            retry_unresolved=True,
            run_id=args.run_id,
            build_id=args.build_id,
        )
        fetched_total += result["fetched"]
        missing_total += result["missing"]
        failed_total += result["failed"]
        skipped_total += result["skipped_fetched"]
        print(
            f"[backfill] batch {index}/{len(batches)}: fetched={result['fetched']} "
            f"missing={result['missing']} failed={result['failed']} "
            f"skipped={result['skipped_fetched']} "
            f"(totals: fetched={fetched_total} missing={missing_total} "
            f"failed={failed_total} skipped={skipped_total})"
        )
        if result["failed_game_pks"]:
            print(f"[backfill]   failed game_pks: {result['failed_game_pks']}")
        if result["missing_game_pks"]:
            print(f"[backfill]   missing game_pks: {result['missing_game_pks']}")

    paths = storage_paths(args.storage_root)
    print("[silver] normalizing")
    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, [])
        normalize_silver(connection)

    print("[certify] building certification artifact")
    with connect_database(paths["database"]) as connection:
        artifact, artifact_path = certify_and_write(
            connection, args.storage_root, args.certifications_dir
        )
    print(f"[certify] status={artifact['status']} -> {artifact_path}")
    return 0 if artifact["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - operator entry
    raise SystemExit(main())
