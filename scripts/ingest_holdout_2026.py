"""ML-010: ingest and certify the 2026 season as the final holdout.

``src/pipelines/certify_historical.py`` (DATA-011) deliberately refuses any
run whose ``seasons`` includes 2026 -- that guard exists because, before
ADR-006 locked the V1 methodology, there was no sanctioned reason to touch
2026 at all. ADR-006 is now accepted and ML-010 is the task authorized to
evaluate 2026 exactly once; ingesting and certifying it is a documented
precondition of that evaluation (see ``tasks/ML-010-2026-final-holdout.md``
"Inputs"), not a development-selection step. This script is therefore the one
sanctioned place 2026 is ingested -- mirroring
``evaluation.holdout``'s role as the one sanctioned place a 2026 row enters a
train/test split.

This script does NOT select, tune, or evaluate anything. It only reuses the
same building blocks ``certify_historical.py`` uses (DATA-002/005/004/007),
scoped to season 2026, against the SAME ``data/mlb.duckdb`` that already holds
the certified 2021-2025 build, so certification runs over the full 2021-2026
Silver contents and the resulting artifact is exactly what
``scripts/holdout_2026.py --certification`` expects.

Historical odds archive (DATA-008/ADR-004) is NOT re-ingested here: the
published ``mlb_odds_dataset.json`` archive's target coverage is 2021-2025
only (ADR-004), so it has no 2026 opening-line rows to add. Market-relative
evaluation for the 2026 holdout is therefore not available from that archive;
ML-010's task file marks market metrics "secondary ... where valid", and it is
not valid here. A live-odds (DATA-003) based 2026 market comparison is a
distinct, out-of-scope follow-up, not part of this ingestion step.

Usage (operator, real network + real MLB-StatsAPI data)::

    python scripts/ingest_holdout_2026.py --storage-root data

Then run the holdout evaluation against the printed certification artifact::

    python scripts/holdout_2026.py --certification <printed path>
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ingestion.mlb import backfill_game_details, ingest_schedule
from ingestion.mlb.statsapi_fetchers import (
    make_game_detail_fetcher,
    make_schedule_fetcher,
)
from ingestion.odds import ingest_the_odds_api_moneylines
from storage import connect_database, storage_paths
from transforms import normalize_silver
from validation import certify_and_write

HOLDOUT_SEASON = 2026
DEFAULT_RUN_ID = "holdout-2026"
DEFAULT_CERTIFICATIONS_DIR = Path("state") / "data-certifications"


def run_holdout_ingestion(
    storage_root: str | Path,
    schedule_fetcher: Any,
    game_detail_fetcher: Any,
    *,
    season: int = HOLDOUT_SEASON,
    run_id: str = DEFAULT_RUN_ID,
    certifications_dir: str | Path = DEFAULT_CERTIFICATIONS_DIR,
    retry_unresolved: bool = False,
    logger: Any = print,
) -> dict[str, Any]:
    """Ingest one season's schedule + game detail, normalize, and certify.

    Idempotent/restartable exactly like DATA-011/DATA-018: ``ingest_schedule``
    and ``backfill_game_details`` are safe to re-run, and re-running
    certification simply re-derives the artifact from current Silver
    contents.
    """
    if season != HOLDOUT_SEASON:
        raise ValueError(
            f"this script ingests only the {HOLDOUT_SEASON} holdout season, got {season}"
        )

    logger(f"[schedule] ingesting season {season}")
    schedule_result = ingest_schedule(storage_root, schedule_fetcher, season=season)

    logger("[detail] backfilling MLB game details")
    backfill = backfill_game_details(
        storage_root,
        game_detail_fetcher,
        seasons=(season,),
        run_id=run_id,
        retry_unresolved=retry_unresolved,
    )

    paths = storage_paths(storage_root)
    logger("[silver] normalizing")
    with connect_database(paths["database"]) as connection:
        # Ensure the live-odds Bronze table exists (empty is fine) so
        # normalize_silver's join against it does not fail on a
        # historical-only build, exactly as certify_historical.py does.
        ingest_the_odds_api_moneylines(connection, [])
        normalize_silver(connection)

    with connect_database(paths["database"]) as connection:
        logger("[certify] building certification artifact over 2021-2026")
        artifact, artifact_path = certify_and_write(
            connection, storage_root, certifications_dir
        )

    logger(f"[certify] status={artifact['status']} -> {artifact_path}")
    return {
        "season": season,
        "schedule": schedule_result,
        "backfill": backfill,
        "certification_status": artifact["status"],
        "certification_path": str(artifact_path),
        "certification": artifact,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest + certify the 2026 holdout season (ML-010 precondition)."
    )
    parser.add_argument("--storage-root", default="data")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--min-interval", type=float, default=0.4)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument(
        "--certifications-dir", default=str(DEFAULT_CERTIFICATIONS_DIR)
    )
    args = parser.parse_args(argv)

    result = run_holdout_ingestion(
        args.storage_root,
        make_schedule_fetcher(min_interval=args.min_interval),
        make_game_detail_fetcher(min_interval=args.min_interval),
        run_id=args.run_id,
        certifications_dir=args.certifications_dir,
        retry_unresolved=args.retry_unresolved,
    )
    print(
        f"certification_status={result['certification_status']} "
        f"artifact={result['certification_path']}"
    )
    return 0 if result["certification_status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - operator entry
    raise SystemExit(main())
