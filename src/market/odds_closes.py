"""Pure helpers for ``odds_closes.jsonl`` strict pre-first-pitch closing snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from app.board import _parse_datetime
from market.clv import (
    close_after_first_pitch,
    close_at_or_before_prediction,
    strict_close_valid,
)

PredictionAnchor = Literal["latest", "earliest"]

STRICT_CLOSING_SOURCE_DEFINITION = (
    "odds_closes.jsonl snapshot for (run_date, game_pk, bookmaker, prediction_timestamp) "
    "where prediction_timestamp < snapshot_timestamp <= game_start_timestamp; "
    "falls back to odds_books.jsonl when no odds_closes row matches."
)

CLOSE_KEY_FIELDS = ("run_date", "game_pk", "bookmaker", "prediction_timestamp")

CAPTURE_METHOD_BACKFILL = "backfill_odds_books"
CAPTURE_METHOD_LIVE = "live_pregame"


def prediction_anchor_label(anchor: PredictionAnchor) -> str:
    if anchor == "earliest":
        return "earliest prediction per game_pk (retrospective CLV population)"
    return "latest prediction per game_pk (board default)"


def _prediction_timestamp_key(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    if value is None:
        return None
    return str(value)


def close_row_key(record: Mapping[str, Any]) -> tuple[Any, Any, Any, str | None]:
    """Stable dedupe key for ``odds_closes.jsonl`` rows."""
    return (
        record.get("run_date"),
        record.get("game_pk"),
        record.get("bookmaker"),
        _prediction_timestamp_key(record.get("prediction_timestamp")),
    )


def index_odds_closes(
    records: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, Any, Any, str | None], Mapping[str, Any]]:
    """Index odds_closes rows by ``(run_date, game_pk, bookmaker, prediction_timestamp)``."""
    indexed: dict[tuple[Any, Any, Any, str | None], Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        indexed[close_row_key(record)] = record
    return indexed


def filter_daily_for_anchor(
    daily_records: Sequence[Mapping[str, Any]],
    *,
    anchor: PredictionAnchor,
) -> list[dict[str, Any]]:
    """Collapse daily rows to one prediction per ``game_pk`` when anchor is earliest."""
    if anchor == "latest":
        return [dict(record) for record in daily_records if isinstance(record, Mapping)]

    earliest: dict[Any, dict[str, Any]] = {}
    for record in daily_records:
        if not isinstance(record, Mapping):
            continue
        game_pk = record.get("game_pk")
        pred_ts = _parse_datetime(record.get("prediction_timestamp"))
        current = earliest.get(game_pk)
        if current is None:
            earliest[game_pk] = dict(record)
            continue
        current_ts = _parse_datetime(current.get("prediction_timestamp"))
        if pred_ts is not None and current_ts is not None:
            if pred_ts < current_ts:
                earliest[game_pk] = dict(record)
        elif pred_ts is not None:
            earliest[game_pk] = dict(record)
    return list(earliest.values())


def validate_close_window(
    prediction_ts: datetime | None,
    snapshot_ts: datetime | None,
    start_ts: datetime | None,
) -> bool:
    """True when ``prediction_ts < snapshot_ts <= start_ts``."""
    return strict_close_valid(prediction_ts, snapshot_ts, start_ts)


def odds_closes_invalid_reason(
    prediction_ts: datetime | None,
    snapshot_ts: datetime | None,
    start_ts: datetime | None,
) -> str:
    """Rejection reason when a keyed ``odds_closes`` row fails the strict window."""
    if snapshot_ts is None:
        return "odds_closes_missing_close_timestamp"
    if close_after_first_pitch(snapshot_ts, start_ts):
        return "odds_closes_close_after_first_pitch"
    if close_at_or_before_prediction(prediction_ts, snapshot_ts):
        return "odds_closes_close_at_or_before_prediction"
    return "odds_closes_invalid_window"


def resolve_close_row(
    *,
    prediction_ts: datetime | None,
    run_date: Any,
    game_pk: Any,
    bookmaker: str,
    game_start_ts: datetime | None,
    odds_closes_index: Mapping[tuple[Any, Any, Any, str | None], Mapping[str, Any]],
    odds_books_index: Mapping[tuple[Any, Any, Any], Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str | None, str | None]:
    """Prefer ``odds_closes``; fall back to ``odds_books`` when valid in strict window.

    Returns ``(row, source, odds_closes_rejection)``. When a keyed ``odds_closes`` row
    exists but fails the strict window, the third value explains why so callers can
    prefer it over ``odds_books`` diagnostic reasons even when fallback also fails.
    """
    pred_key = _prediction_timestamp_key(prediction_ts)
    close_key = (run_date, game_pk, bookmaker, pred_key)
    odds_closes_rejection: str | None = None
    odds_close = odds_closes_index.get(close_key)
    if odds_close is not None:
        close_ts = _parse_datetime(odds_close.get("snapshot_timestamp"))
        if validate_close_window(prediction_ts, close_ts, game_start_ts):
            return odds_close, "odds_closes", None
        odds_closes_rejection = odds_closes_invalid_reason(
            prediction_ts, close_ts, game_start_ts
        )

    odds_row = odds_books_index.get((game_pk, run_date, bookmaker))
    if odds_row is not None:
        close_ts = _parse_datetime(odds_row.get("snapshot_timestamp"))
        if validate_close_window(prediction_ts, close_ts, game_start_ts):
            return odds_row, "odds_books", None
    return None, None, odds_closes_rejection


def _bookmaker_from_source(source: Any, *, default: str = "draftkings") -> str:
    if not isinstance(source, str) or not source.strip():
        return default
    if ":" in source:
        return source.rsplit(":", 1)[-1].strip() or default
    return source.strip()


def backfill_closes_from_odds_books(
    daily_records: Sequence[Mapping[str, Any]],
    odds_books_records: Sequence[Mapping[str, Any]],
    *,
    anchor: PredictionAnchor = "earliest",
) -> list[dict[str, Any]]:
    """Build strict-window odds_closes rows from existing odds_books snapshots."""
    predictions = filter_daily_for_anchor(daily_records, anchor=anchor)
    odds_index: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    for record in odds_books_records:
        if not isinstance(record, Mapping):
            continue
        key = (record.get("game_pk"), record.get("run_date"), record.get("bookmaker"))
        odds_index[key] = record

    closes: list[dict[str, Any]] = []
    for prediction in predictions:
        bookmaker = _bookmaker_from_source(prediction.get("source"))
        game_pk = prediction.get("game_pk")
        run_date = prediction.get("run_date")
        odds_row = odds_index.get((game_pk, run_date, bookmaker))
        if odds_row is None:
            continue

        prediction_ts = _parse_datetime(prediction.get("prediction_timestamp"))
        start_ts = _parse_datetime(prediction.get("game_start_timestamp"))
        snapshot_ts = _parse_datetime(odds_row.get("snapshot_timestamp"))
        if not validate_close_window(prediction_ts, snapshot_ts, start_ts):
            continue

        home = odds_row.get("home_american")
        away = odds_row.get("away_american")
        if not isinstance(home, int) or isinstance(home, bool):
            continue
        if not isinstance(away, int) or isinstance(away, bool):
            continue

        closes.append(
            {
                "run_date": run_date,
                "game_pk": game_pk,
                "bookmaker": bookmaker,
                "prediction_timestamp": prediction_ts,
                "home_american": home,
                "away_american": away,
                "snapshot_timestamp": snapshot_ts,
                "source": odds_row.get("source", "the_odds_api"),
                "capture_method": CAPTURE_METHOD_BACKFILL,
            }
        )
    closes.sort(key=lambda row: (row.get("run_date"), row.get("game_pk"), row.get("bookmaker")))
    return closes


def build_live_close_record(
    *,
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    run_date: Any,
    now: datetime,
) -> dict[str, Any] | None:
    """Validate and shape one live capture row for ``odds_closes.jsonl``."""
    bookmaker = snapshot.get("bookmaker")
    if not isinstance(bookmaker, str) or not bookmaker:
        return None

    prediction_ts = _parse_datetime(prediction.get("prediction_timestamp"))
    start_ts = _parse_datetime(prediction.get("game_start_timestamp"))
    snapshot_ts = _parse_datetime(snapshot.get("snapshot_timestamp"))
    if snapshot_ts is None:
        snapshot_ts = now
    if not validate_close_window(prediction_ts, snapshot_ts, start_ts):
        return None

    home = snapshot.get("home_american")
    away = snapshot.get("away_american")
    if not isinstance(home, int) or isinstance(home, bool):
        return None
    if not isinstance(away, int) or isinstance(away, bool):
        return None

    return {
        "run_date": run_date,
        "game_pk": prediction.get("game_pk"),
        "bookmaker": bookmaker,
        "prediction_timestamp": prediction_ts,
        "home_american": home,
        "away_american": away,
        "snapshot_timestamp": snapshot_ts,
        "source": snapshot.get("source", "the_odds_api"),
        "capture_method": CAPTURE_METHOD_LIVE,
    }
