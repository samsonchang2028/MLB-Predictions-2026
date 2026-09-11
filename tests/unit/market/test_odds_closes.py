"""Unit tests for odds_closes helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.daily_predictions import append_jsonl_records

from market.odds_closes import (
    CAPTURE_METHOD_BACKFILL,
    CLOSE_KEY_FIELDS,
    backfill_closes_from_odds_books,
    build_live_close_record,
    close_row_key,
    filter_daily_for_anchor,
    index_odds_closes,
    resolve_close_row,
    validate_close_window,
)


def _prediction(
    game_pk: int,
    *,
    offset_hours: int = 0,
    run_date: str = "2026-08-01",
) -> dict:
    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc) + timedelta(hours=offset_hours)
    return {
        "game_pk": game_pk,
        "run_date": run_date,
        "prediction_timestamp": base.isoformat(),
        "game_start_timestamp": (base + timedelta(hours=4)).isoformat(),
        "source": "the_odds_api:draftkings",
        "home_american": -110,
        "away_american": 100,
    }


def _odds_row(
    prediction: dict,
    *,
    snapshot_offset_minutes: int = 60,
) -> dict:
    pred_ts = datetime.fromisoformat(prediction["prediction_timestamp"])
    return {
        "game_pk": prediction["game_pk"],
        "run_date": prediction["run_date"],
        "bookmaker": "draftkings",
        "home_american": -120,
        "away_american": 110,
        "snapshot_timestamp": (pred_ts + timedelta(minutes=snapshot_offset_minutes)).isoformat(),
        "source": "the_odds_api",
    }


def test_validate_close_window() -> None:
    pred = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)
    valid = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
    assert validate_close_window(pred, valid, start) is True
    assert validate_close_window(pred, pred, start) is False
    assert validate_close_window(pred, start + timedelta(minutes=1), start) is False


def test_filter_daily_for_anchor_earliest() -> None:
    older = _prediction(100, offset_hours=0)
    newer = _prediction(100, offset_hours=6)
    other = _prediction(101, offset_hours=0)
    filtered = filter_daily_for_anchor([older, newer, other], anchor="earliest")
    by_pk = {row["game_pk"]: row for row in filtered}
    assert len(filtered) == 2
    assert by_pk[100]["prediction_timestamp"] == older["prediction_timestamp"]


def test_backfill_from_odds_books_strict_window() -> None:
    pred = _prediction(200)
    odds = [_odds_row(pred, snapshot_offset_minutes=30)]
    closes = backfill_closes_from_odds_books([pred], odds, anchor="earliest")
    assert len(closes) == 1
    assert closes[0]["capture_method"] == CAPTURE_METHOD_BACKFILL
    assert closes[0]["home_american"] == -120


def test_backfill_rejects_close_before_prediction() -> None:
    pred = _prediction(201)
    odds = [_odds_row(pred, snapshot_offset_minutes=-30)]
    closes = backfill_closes_from_odds_books([pred], odds, anchor="earliest")
    assert closes == []


def test_index_and_resolve_prefers_odds_closes() -> None:
    pred = _prediction(300)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    start_ts = datetime.fromisoformat(pred["game_start_timestamp"])
    odds_books = [_odds_row(pred, snapshot_offset_minutes=30)]
    odds_closes = [
        {
            "run_date": pred["run_date"],
            "game_pk": pred["game_pk"],
            "bookmaker": "draftkings",
            "prediction_timestamp": pred_ts.isoformat(),
            "home_american": -130,
            "away_american": 115,
            "snapshot_timestamp": (pred_ts + timedelta(minutes=45)).isoformat(),
            "source": "the_odds_api",
            "capture_method": CAPTURE_METHOD_BACKFILL,
        }
    ]
    books_index = {(row["game_pk"], row["run_date"], row["bookmaker"]): row for row in odds_books}
    closes_index = index_odds_closes(odds_closes)
    row, source, odds_closes_rejection = resolve_close_row(
        prediction_ts=pred_ts,
        run_date=pred["run_date"],
        game_pk=pred["game_pk"],
        bookmaker="draftkings",
        game_start_ts=start_ts,
        odds_closes_index=closes_index,
        odds_books_index=books_index,
    )
    assert source == "odds_closes"
    assert row is not None
    assert row["home_american"] == -130
    assert odds_closes_rejection is None


def test_resolve_falls_back_to_odds_books() -> None:
    pred = _prediction(301)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    start_ts = datetime.fromisoformat(pred["game_start_timestamp"])
    odds_books = [_odds_row(pred, snapshot_offset_minutes=30)]
    books_index = {(row["game_pk"], row["run_date"], row["bookmaker"]): row for row in odds_books}
    row, source, odds_closes_rejection = resolve_close_row(
        prediction_ts=pred_ts,
        run_date=pred["run_date"],
        game_pk=pred["game_pk"],
        bookmaker="draftkings",
        game_start_ts=start_ts,
        odds_closes_index={},
        odds_books_index=books_index,
    )
    assert source == "odds_books"
    assert row is not None
    assert odds_closes_rejection is None


def test_resolve_invalid_keyed_close_reports_rejection() -> None:
    pred = _prediction(302)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    start_ts = datetime.fromisoformat(pred["game_start_timestamp"])
    odds_books = [_odds_row(pred, snapshot_offset_minutes=300)]
    odds_closes = [
        {
            "run_date": pred["run_date"],
            "game_pk": pred["game_pk"],
            "bookmaker": "draftkings",
            "prediction_timestamp": pred_ts.isoformat(),
            "home_american": -130,
            "away_american": 115,
            "snapshot_timestamp": (pred_ts - timedelta(minutes=30)).isoformat(),
            "source": "the_odds_api",
            "capture_method": CAPTURE_METHOD_BACKFILL,
        }
    ]
    books_index = {(row["game_pk"], row["run_date"], row["bookmaker"]): row for row in odds_books}
    closes_index = index_odds_closes(odds_closes)
    row, source, odds_closes_rejection = resolve_close_row(
        prediction_ts=pred_ts,
        run_date=pred["run_date"],
        game_pk=pred["game_pk"],
        bookmaker="draftkings",
        game_start_ts=start_ts,
        odds_closes_index=closes_index,
        odds_books_index=books_index,
    )
    assert row is None
    assert source is None
    assert odds_closes_rejection == "odds_closes_close_at_or_before_prediction"


def test_append_odds_closes_idempotent_and_conflict(tmp_path) -> None:
    pred = _prediction(303)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    record = {
        "run_date": pred["run_date"],
        "game_pk": pred["game_pk"],
        "bookmaker": "draftkings",
        "prediction_timestamp": pred_ts.isoformat(),
        "home_american": -120,
        "away_american": 110,
        "snapshot_timestamp": (pred_ts + timedelta(minutes=30)).isoformat(),
        "source": "the_odds_api",
        "capture_method": CAPTURE_METHOD_BACKFILL,
    }
    path = tmp_path / "odds_closes.jsonl"

    written_first = append_jsonl_records(path, [record], key_fields=CLOSE_KEY_FIELDS)
    written_second = append_jsonl_records(path, [record], key_fields=CLOSE_KEY_FIELDS)

    assert written_first == 1
    assert written_second == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    conflicting = {**record, "home_american": -125}
    with pytest.raises(ValueError, match="conflicting re-write"):
        append_jsonl_records(path, [conflicting], key_fields=CLOSE_KEY_FIELDS)


def test_close_row_key_stable() -> None:
    pred = _prediction(400)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    key = close_row_key(
        {
            "run_date": pred["run_date"],
            "game_pk": pred["game_pk"],
            "bookmaker": "draftkings",
            "prediction_timestamp": pred_ts,
        }
    )
    assert key == (pred["run_date"], pred["game_pk"], "draftkings", pred_ts.isoformat())


def test_build_live_close_record() -> None:
    pred = _prediction(500)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    now = pred_ts + timedelta(minutes=90)
    snapshot = {
        "bookmaker": "draftkings",
        "home_american": -115,
        "away_american": 105,
        "snapshot_timestamp": now.isoformat(),
        "source": "the_odds_api",
    }
    record = build_live_close_record(
        prediction=pred,
        snapshot=snapshot,
        run_date=pred["run_date"],
        now=now,
    )
    assert record is not None
    assert record["capture_method"] == "live_pregame"


def test_build_live_close_record_rejects_invalid_window() -> None:
    pred = _prediction(501)
    pred_ts = datetime.fromisoformat(pred["prediction_timestamp"])
    snapshot = {
        "bookmaker": "draftkings",
        "home_american": -115,
        "away_american": 105,
        "snapshot_timestamp": (pred_ts - timedelta(minutes=5)).isoformat(),
        "source": "the_odds_api",
    }
    record = build_live_close_record(
        prediction=pred,
        snapshot=snapshot,
        run_date=pred["run_date"],
        now=pred_ts - timedelta(minutes=5),
    )
    assert record is None
