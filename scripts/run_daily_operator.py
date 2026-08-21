"""OPS-001: stable homelab entrypoint for daily prediction operations.

The scheduler chooses only a logical stage. This wrapper owns the internal
sequence and delegates prediction and result semantics to the existing
operators rather than duplicating them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts import daily_predictions, enrich_prediction_results  # noqa: E402
from storage import connect_database  # noqa: E402
from transforms import normalize_silver  # noqa: E402

PACIFIC = ZoneInfo("America/Los_Angeles")
STAGES = ("all", "predict", "enrich")


def _pacific_today() -> date:
    return datetime.now(PACIFIC).date()


def _log(
    *,
    run_id: str,
    run_date: date,
    stage: str,
    status: str,
    **details: Any,
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "run_date": str(run_date),
        "stage": stage,
        "status": status,
        **details,
    }
    print(f"[operator] {json.dumps(record, sort_keys=True)}", flush=True)


def _run_stage(
    name: str,
    action: Callable[[], int | None],
    *,
    run_id: str,
    run_date: date,
) -> int:
    _log(run_id=run_id, run_date=run_date, stage=name, status="started")
    try:
        result = action()
    except Exception as error:  # noqa: BLE001 - top-level operator boundary
        _log(
            run_id=run_id,
            run_date=run_date,
            stage=name,
            status="failed",
            error=f"{type(error).__name__}: {error}",
        )
        return 1
    exit_code = int(result or 0)
    _log(
        run_id=run_id,
        run_date=run_date,
        stage=name,
        status="completed" if exit_code == 0 else "failed",
        exit_code=exit_code,
    )
    return exit_code


def _refresh_and_normalize(database: str, run_date: date) -> int:
    refreshed = enrich_prediction_results.refresh_schedule_results(database, run_date)
    print(
        "[schedule-refresh] "
        f"games_seen={refreshed.get('games_seen')} "
        f"payload={refreshed.get('payload_sha256')}",
        flush=True,
    )
    with connect_database(database) as connection:
        normalize_silver(connection)
    print("[schedule-refresh] silver_normalized=true", flush=True)
    return 0


def _prediction_argv(args: argparse.Namespace, run_date: date) -> list[str]:
    argv = [
        "--date",
        str(run_date),
        "--database",
        args.database,
        "--certification",
        args.certification,
        "--output",
        args.predictions,
    ]
    if args.prediction_timestamp:
        argv.extend(["--prediction-timestamp", args.prediction_timestamp])
    if args.odds_json:
        argv.extend(["--odds-json", args.odds_json])
    if args.skip_detail_refresh:
        argv.append("--skip-detail-refresh")
    if args.enable_simulation:
        argv.append("--enable-simulation")
    return argv


def _enrichment_argv(
    args: argparse.Namespace, run_date: date, *, schedule_already_refreshed: bool
) -> list[str]:
    argv = [
        "--date",
        str(run_date),
        "--database",
        args.database,
        "--predictions",
        args.predictions,
        "--journal",
        args.journal,
    ]
    if args.enrichment_timestamp:
        argv.extend(["--enrichment-timestamp", args.enrichment_timestamp])
    if args.skip_schedule_refresh or schedule_already_refreshed:
        argv.append("--skip-schedule-refresh")
    return argv


def run(args: argparse.Namespace) -> int:
    run_date = date.fromisoformat(args.date) if args.date else _pacific_today()
    run_id = args.run_id or (
        f"ops-{run_date}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    _log(run_id=run_id, run_date=run_date, stage="operator", status="started", mode=args.stage)

    schedule_refreshed = False
    if args.stage in ("all", "predict") and not args.skip_schedule_refresh:
        code = _run_stage(
            "schedule_refresh",
            lambda: _refresh_and_normalize(args.database, run_date),
            run_id=run_id,
            run_date=run_date,
        )
        if code:
            return code
        schedule_refreshed = True

    if args.stage in ("all", "predict"):
        code = _run_stage(
            "prediction",
            lambda: daily_predictions.main(_prediction_argv(args, run_date)),
            run_id=run_id,
            run_date=run_date,
        )
        if code:
            return code

    if args.stage in ("all", "enrich"):
        code = _run_stage(
            "result_enrichment",
            lambda: enrich_prediction_results.main(
                _enrichment_argv(
                    args,
                    run_date,
                    schedule_already_refreshed=schedule_refreshed,
                )
            ),
            run_id=run_id,
            run_date=run_date,
        )
        if code:
            return code

    _log(run_id=run_id, run_date=run_date, stage="operator", status="completed", exit_code=0)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the restartable MLB homelab daily operator."
    )
    parser.add_argument("--stage", choices=STAGES, default="all")
    parser.add_argument("--date", default=None, help="MLB official date; defaults to today in Pacific time")
    parser.add_argument("--run-id", default=None, help="Optional operator log identifier")
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument(
        "--certification",
        default="state/data-certifications/certification-PASS-a910017bac839af5.json",
    )
    parser.add_argument("--predictions", default="state/predictions/daily.jsonl")
    parser.add_argument("--journal", default="state/predictions/journal.jsonl")
    parser.add_argument("--prediction-timestamp", default=None)
    parser.add_argument("--enrichment-timestamp", default=None)
    parser.add_argument("--odds-json", default=None, help="Offline prediction replay input")
    parser.add_argument("--skip-schedule-refresh", action="store_true")
    parser.add_argument("--skip-detail-refresh", action="store_true")
    parser.add_argument("--enable-simulation", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Exception as error:  # noqa: BLE001 - command-line operator boundary
        run_date = date.fromisoformat(args.date) if args.date else _pacific_today()
        _log(
            run_id=args.run_id or "uninitialized",
            run_date=run_date,
            stage="operator",
            status="failed",
            error=f"{type(error).__name__}: {error}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
