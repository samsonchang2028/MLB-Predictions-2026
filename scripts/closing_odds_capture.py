"""MARKET-009 - capture pre-first-pitch closing odds for strict CLV evaluation.

Runs frequently (e.g. every 10-15 minutes) near each game's first pitch, mirroring
``kalshi_pregame_capture.py``. Writes append-only rows to ``odds_closes.jsonl``
keyed by ``(run_date, game_pk, bookmaker, prediction_timestamp)``.

Also supports retrospective population via ``--backfill-from-odds-books``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_predictions as dp  # noqa: E402
from market.odds_closes import (  # noqa: E402
    CLOSE_KEY_FIELDS,
    PredictionAnchor,
    backfill_closes_from_odds_books,
    build_live_close_record,
    close_row_key,
)

DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_DAILY = Path("state") / "predictions" / "daily.jsonl"
DEFAULT_ODDS_BOOKS = Path("state") / "predictions" / "odds_books.jsonl"
DEFAULT_ODDS_CLOSES = Path("state") / "predictions" / "odds_closes.jsonl"
DEFAULT_BOOKMAKER = "draftkings"

DEFAULT_WINDOW_START_MINUTES = 60.0
DEFAULT_WINDOW_END_MINUTES = 15.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_schedule(database: str | Path, run_date: date) -> list[dict[str, Any]]:
    with duckdb.connect(str(database), read_only=True) as connection:
        schedule = dp._dict_rows(
            connection,
            """
            SELECT game_pk, game_date, game_date AS game_start_timestamp, source_game_json
            FROM silver.games
            WHERE official_date = ?
              AND game_type = 'R'
              AND abstract_game_state IN ('Preview', 'Live')
            ORDER BY game_date, game_pk
            """,
            [run_date],
        )
    for row in schedule:
        row["game_start_timestamp"] = dp._utc_instant(row["game_start_timestamp"])
    return schedule


def latest_predictions_for_date(
    daily_records: Sequence[Mapping[str, Any]],
    run_date: date,
) -> dict[Any, dict[str, Any]]:
    """Latest prediction per ``game_pk`` for the given ``run_date``."""
    run_date_str = str(run_date)
    latest: dict[Any, dict[str, Any]] = {}
    for record in daily_records:
        if str(record.get("run_date")) != run_date_str:
            continue
        game_pk = record.get("game_pk")
        current = latest.get(game_pk)
        if current is None:
            latest[game_pk] = dict(record)
            continue
        row_ts = dp.parse_timestamp(str(record.get("prediction_timestamp")))
        current_ts = dp.parse_timestamp(str(current.get("prediction_timestamp")))
        if row_ts >= current_ts:
            latest[game_pk] = dict(record)
    return latest


def already_captured_keys(path: Path, run_date: date) -> set[tuple[Any, ...]]:
    if not path.exists():
        return set()
    run_date_str = str(run_date)
    captured: set[tuple[Any, ...]] = set()
    for record in _read_jsonl(path):
        if str(record.get("run_date")) == run_date_str:
            captured.add(close_row_key(record))
    return captured


def games_due_for_capture(
    schedule: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    already_captured: Iterable[tuple[Any, ...]],
    predictions_by_pk: Mapping[Any, Mapping[str, Any]],
    bookmaker: str,
    run_date: date,
    window_start_minutes: float = DEFAULT_WINDOW_START_MINUTES,
    window_end_minutes: float = DEFAULT_WINDOW_END_MINUTES,
) -> list[dict[str, Any]]:
    captured = set(already_captured)
    due: list[dict[str, Any]] = []
    run_date_str = str(run_date)
    for game in schedule:
        game_pk = game["game_pk"]
        prediction = predictions_by_pk.get(game_pk)
        if prediction is None:
            continue
        pred_ts = dp.parse_timestamp(str(prediction.get("prediction_timestamp")))
        key = (run_date_str, game_pk, bookmaker, pred_ts.isoformat())
        if key in captured:
            continue
        minutes_until_first_pitch = (
            game["game_start_timestamp"] - now
        ).total_seconds() / 60.0
        if window_end_minutes <= minutes_until_first_pitch <= window_start_minutes:
            due.append(dict(game))
    return due


def _run_backfill(args: argparse.Namespace) -> int:
    daily_records = _read_jsonl(Path(args.daily))
    odds_books_records = _read_jsonl(Path(args.odds_books))
    anchor: PredictionAnchor = args.anchor
    closes = backfill_closes_from_odds_books(
        daily_records,
        odds_books_records,
        anchor=anchor,
    )
    print(
        f"[backfill] anchor={anchor} candidates={len(closes)} "
        f"daily={len(daily_records)} odds_books={len(odds_books_records)}",
        flush=True,
    )
    if not closes:
        print("[done] no strict-window closes found for backfill", flush=True)
        return 0

    output = Path(args.odds_closes_output)
    written = dp.append_jsonl_records(
        output,
        closes,
        key_fields=CLOSE_KEY_FIELDS,
        on_conflict="raise",
    )
    print(f"[odds-closes] written={written} output={output}", flush=True)
    return 0


def _run_live_capture(args: argparse.Namespace) -> int:
    run_date = dp.parse_date(args.date)
    now = dp.parse_timestamp(args.now)

    schedule = load_schedule(args.database, run_date)
    print(f"[schedule] games={len(schedule)} run_date={run_date}", flush=True)
    if not schedule:
        print("[done] no active regular-season games found for date", flush=True)
        return 0

    daily_records = _read_jsonl(Path(args.daily))
    predictions_by_pk = latest_predictions_for_date(daily_records, run_date)
    if not predictions_by_pk:
        print("[done] no daily predictions found for run_date", flush=True)
        return 0

    captured = already_captured_keys(Path(args.odds_closes_output), run_date)
    due = games_due_for_capture(
        schedule,
        now=now,
        already_captured=captured,
        predictions_by_pk=predictions_by_pk,
        bookmaker=args.bookmaker,
        run_date=run_date,
        window_start_minutes=args.window_start_minutes,
        window_end_minutes=args.window_end_minutes,
    )
    print(
        f"[due] games={len(due)} already_captured={len(captured)} "
        f"window=({args.window_end_minutes}, {args.window_start_minutes}) minutes-before-first-pitch",
        flush=True,
    )
    if not due:
        print("[done] no games due for closing odds capture right now", flush=True)
        return 0

    due_game_pks = {game["game_pk"] for game in due}
    due_schedule = [game for game in schedule if game["game_pk"] in due_game_pks]

    if args.odds_json:
        payload = json.loads(Path(args.odds_json).read_text(encoding="utf-8"))
        print(f"[odds] loaded payload={args.odds_json}", flush=True)
    else:
        api_key = os.environ.get("THE_ODDS_API_KEY")
        if not api_key:
            print("[odds] THE_ODDS_API_KEY not set, skipping live fetch", flush=True)
            return 0
        print(f"[odds] fetching bookmaker={args.bookmaker}", flush=True)
        try:
            if args.all_books:
                payload = dp.fetch_odds_payload(
                    api_key, regions=args.regions, bookmakers=None, timeout=args.timeout
                )
            else:
                payload = dp.fetch_odds_payload(
                    api_key,
                    regions=args.regions,
                    bookmakers=args.bookmaker,
                    timeout=args.timeout,
                )
        except RuntimeError as exc:
            print(f"[odds] fetch failed, skipping this run: {exc}", flush=True)
            return 0

    if args.all_books:
        snapshots, stats = dp.all_book_snapshots_for_schedule(payload, due_schedule)
        print(f"[odds] mapped_game_books={stats.get('mapped_game_books', 0)}", flush=True)
    else:
        single, stats = dp.odds_snapshots_for_schedule(
            payload,
            due_schedule,
            bookmaker=args.bookmaker,
        )
        snapshots = {
            (game_pk, args.bookmaker): {
                "game_pk": game_pk,
                "bookmaker": args.bookmaker,
                **snapshot,
            }
            for game_pk, snapshot in single.items()
        }
        print(f"[odds] mapped_games={stats.get('mapped_games', 0)}", flush=True)

    records: list[dict[str, Any]] = []
    for game_pk in sorted(due_game_pks):
        prediction = predictions_by_pk.get(game_pk)
        if prediction is None:
            continue
        if args.all_books:
            game_snapshots = [
                snapshot
                for (pk, _book), snapshot in snapshots.items()
                if pk == game_pk
            ]
        else:
            snapshot = snapshots.get((game_pk, args.bookmaker))
            game_snapshots = [snapshot] if snapshot is not None else []

        for snapshot in game_snapshots:
            record = build_live_close_record(
                prediction=prediction,
                snapshot=snapshot,
                run_date=run_date,
                now=now,
            )
            if record is not None:
                records.append(record)

    written = dp.append_jsonl_records(
        Path(args.odds_closes_output),
        records,
        key_fields=CLOSE_KEY_FIELDS,
        on_conflict="raise",
    )
    print(f"[odds-closes] written={written} output={args.odds_closes_output}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture pre-first-pitch closing odds for strict CLV evaluation. "
            "Run frequently via scheduler, or backfill from odds_books.jsonl."
        )
    )
    parser.add_argument("--date", default=None, help="Official MLB date YYYY-MM-DD; default local today.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--odds-books", default=str(DEFAULT_ODDS_BOOKS))
    parser.add_argument("--odds-closes-output", default=str(DEFAULT_ODDS_CLOSES))
    parser.add_argument("--bookmaker", default=DEFAULT_BOOKMAKER)
    parser.add_argument("--regions", default="us")
    parser.add_argument("--all-books", action="store_true", help="Capture every book on the slate.")
    parser.add_argument(
        "--backfill-from-odds-books",
        action="store_true",
        help="Retrospectively populate odds_closes.jsonl from odds_books snapshots.",
    )
    parser.add_argument(
        "--anchor",
        choices=("latest", "earliest"),
        default="earliest",
        help="Prediction anchor for backfill (default earliest for strict CLV population).",
    )
    parser.add_argument(
        "--odds-json",
        default=None,
        help="Optional saved Odds API payload for offline replay/tests.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO timestamp with timezone; default now UTC (tests/replay).",
    )
    parser.add_argument("--window-start-minutes", type=float, default=DEFAULT_WINDOW_START_MINUTES)
    parser.add_argument("--window-end-minutes", type=float, default=DEFAULT_WINDOW_END_MINUTES)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    if args.backfill_from_odds_books:
        return _run_backfill(args)
    return _run_live_capture(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
