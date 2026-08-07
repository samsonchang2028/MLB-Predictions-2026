"""DATA-011 - produce and certify the real 2021-2025 historical MLB dataset.

Sequences the existing building blocks into one idempotent, restartable run:

1. MLB schedule ingest per development season (DATA-002).
2. MLB game-detail backfill (DATA-005) with a stable ``run_id`` so restarts are
   safe (DATA-010).
3. Silver normalization (DATA-004/005).
4. Historical odds archive ingest (DATA-008) from the published asset file.
5. Odds -> ``game_pk`` mapping + coverage (DATA-009).
6. PASS/FAIL certification artifact (DATA-007) written under
   ``state/data-certifications/``.

2026 is the untouched holdout (ADR-003/ADR-004) and is refused here.

The wrapper-backed fetchers and the odds-ingest step are injectable so the whole
runner can be exercised end-to-end from fixtures with no network. ``main``
constructs the real MLB-StatsAPI-backed fetchers for operator execution.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ingestion.mlb import backfill_game_details, ingest_schedule
from ingestion.odds import ingest_the_odds_api_moneylines
from ingestion.odds.historical import ingest_historical_odds_archive
from storage import connect_database, storage_paths
from transforms import normalize_silver
from validation import certify_and_write, validate_odds_archive

DEV_SEASONS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
HOLDOUT_SEASON = 2026
DEFAULT_RUN_ID = "historical-2021-2025"
DEFAULT_CERTIFICATIONS_DIR = Path("state") / "data-certifications"
DEFAULT_ODDS_ARCHIVE = "mlb_odds_dataset.json"

ScheduleFetcher = Callable[[Mapping[str, object]], bytes]
GameDetailFetcher = Callable[[str, Mapping[str, object]], "bytes | None"]
OddsIngest = Callable[[Any, Any], dict]


def run_historical_certification(
    storage_root: str | Path,
    odds_archive_path: str | Path,
    schedule_fetcher: ScheduleFetcher,
    game_detail_fetcher: GameDetailFetcher,
    *,
    seasons: Iterable[int] = DEV_SEASONS,
    run_id: str = DEFAULT_RUN_ID,
    certifications_dir: str | Path = DEFAULT_CERTIFICATIONS_DIR,
    retry_unresolved: bool = False,
    odds_ingest: OddsIngest = ingest_historical_odds_archive,
    logger: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Build and certify the historical dataset; return a structured summary.

    ``odds_ingest`` is injectable purely so the runner is testable without the
    80MB published archive; production uses the real DATA-008 ingest which
    verifies the published SHA-256.
    """
    seasons = tuple(seasons)
    if HOLDOUT_SEASON in seasons:
        raise ValueError(
            f"{HOLDOUT_SEASON} is the untouched holdout and must not be ingested here"
        )

    schedule_results = []
    for season in seasons:
        logger(f"[schedule] ingesting season {season}")
        schedule_results.append(
            ingest_schedule(storage_root, schedule_fetcher, season=season)
        )

    logger("[detail] backfilling MLB game details")
    backfill = backfill_game_details(
        storage_root,
        game_detail_fetcher,
        seasons=seasons,
        run_id=run_id,
        retry_unresolved=retry_unresolved,
    )

    paths = storage_paths(storage_root)
    logger("[silver] normalizing")
    with connect_database(paths["database"]) as connection:
        # normalize_silver reads the DATA-003 live-odds Bronze table. A
        # historical-only build may never have ingested live odds, so ensure the
        # table exists (empty) via its owning ingest rather than duplicating DDL.
        ingest_the_odds_api_moneylines(connection, [])
        normalize_silver(connection)

    logger("[odds] ingesting historical archive")
    odds_ingest_result = odds_ingest(storage_root, odds_archive_path)

    with connect_database(paths["database"]) as connection:
        logger("[odds] validating archive + mapping to game_pk")
        odds_validation = validate_odds_archive(connection, storage_root)
        logger("[certify] building certification artifact")
        artifact, artifact_path = certify_and_write(
            connection, storage_root, certifications_dir
        )

    logger(f"[certify] status={artifact['status']} -> {artifact_path}")
    return {
        "seasons": seasons,
        "schedule": schedule_results,
        "backfill": backfill,
        "odds_ingest": odds_ingest_result,
        "odds_validation_summary": odds_validation["summary"],
        "odds_coverage": odds_validation["coverage"],
        "certification_status": artifact["status"],
        "certification_path": str(artifact_path),
        "certification": artifact,
    }


def main(argv: list[str] | None = None) -> int:
    """Operator entry point: run the real MLB-StatsAPI-backed certification."""
    from ingestion.mlb.statsapi_fetchers import (
        make_game_detail_fetcher,
        make_schedule_fetcher,
    )

    parser = argparse.ArgumentParser(description="Build + certify 2021-2025 MLB data")
    parser.add_argument("--storage-root", default="data")
    parser.add_argument("--odds-archive", default=DEFAULT_ODDS_ARCHIVE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument(
        "--seasons", default=",".join(str(s) for s in DEV_SEASONS),
        help="comma-separated seasons (2026 is refused)",
    )
    parser.add_argument("--min-interval", type=float, default=0.4)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument(
        "--certifications-dir", default=str(DEFAULT_CERTIFICATIONS_DIR)
    )
    args = parser.parse_args(argv)

    seasons = tuple(int(s) for s in args.seasons.split(",") if s.strip())
    result = run_historical_certification(
        args.storage_root,
        args.odds_archive,
        make_schedule_fetcher(min_interval=args.min_interval),
        make_game_detail_fetcher(min_interval=args.min_interval),
        seasons=seasons,
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
