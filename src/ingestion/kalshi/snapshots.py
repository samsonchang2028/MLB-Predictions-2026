"""Parse and persist timestamped Kalshi MLB market snapshots.

Kalshi models one game as an event containing two independent single-sided
binary markets (one per team, e.g. "will Seattle win"), not a single
home/away pair the way a sportsbook quotes a moneyline. Each market carries
its own yes/no bid/ask, confirmed live against
`GET https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME`
(see the task handoff for the verified field names). This module intentionally
does not try to force Kalshi's shape into the sportsbook odds table.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import duckdb


class KalshiDataError(ValueError):
    """Raised when a Kalshi payload cannot form a trustworthy snapshot."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KalshiDataError(f"{field} is required and must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise KalshiDataError(f"{field} must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KalshiDataError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _price(value: Any, field: str) -> Decimal:
    # Kalshi quotes prices as decimal-dollar strings (e.g. "0.4400"), not cents
    # or floats.
    if not isinstance(value, str) or not value.strip():
        raise KalshiDataError(f"{field} is required and must be a decimal-dollar string")
    try:
        price = Decimal(value)
    except InvalidOperation as error:
        raise KalshiDataError(f"{field} must be a valid decimal price") from error
    if price < 0 or price > 1:
        raise KalshiDataError(f"{field} must be between 0 and 1 dollars")
    return price


def parse_kalshi_market_snapshots(
    payload: Any, *, source: str = "kalshi"
) -> list[dict[str, Any]]:
    """Map a Kalshi `GET /markets` response to canonical per-team observations."""
    source_name = _required_text(source, "source")
    if not isinstance(payload, dict):
        raise KalshiDataError("payload must be an object with a 'markets' list")
    markets = payload.get("markets")
    if not isinstance(markets, list) or not all(isinstance(item, dict) for item in markets):
        raise KalshiDataError("payload.markets must be a list of objects")

    # One raw payload -> one immutable hash, shared by every row it produces
    # (mirrors bronze.mlb_game_detail_payloads: the hash identifies the raw
    # source response, not any single parsed row).
    payload_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    snapshots: list[dict[str, Any]] = []
    for index, market in enumerate(markets):
        prefix = f"payload.markets[{index}]"
        market_ticker = _required_text(market.get("ticker"), f"{prefix}.ticker")
        event_ticker = _required_text(market.get("event_ticker"), f"{prefix}.event_ticker")
        # yes_sub_title is Kalshi's own "shortened title for the yes side of
        # this market" (docs.kalshi.com) — i.e. the team this market's "yes"
        # resolves for. This is the natural `side` for a per-team market.
        side = _required_text(market.get("yes_sub_title"), f"{prefix}.yes_sub_title")
        yes_bid = _price(market.get("yes_bid_dollars"), f"{prefix}.yes_bid_dollars")
        yes_ask = _price(market.get("yes_ask_dollars"), f"{prefix}.yes_ask_dollars")
        no_bid = _price(market.get("no_bid_dollars"), f"{prefix}.no_bid_dollars")
        no_ask = _price(market.get("no_ask_dollars"), f"{prefix}.no_ask_dollars")
        snapshot_timestamp = _timestamp(market.get("updated_time"), f"{prefix}.updated_time")

        snapshots.append(
            {
                "source": source_name,
                "market_ticker": market_ticker,
                "event_ticker": event_ticker,
                "side": side,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "snapshot_timestamp": snapshot_timestamp,
                "source_payload_sha256": payload_sha256,
            }
        )

    return snapshots


def ingest_kalshi_market_snapshots(
    connection: duckdb.DuckDBPyConnection,
    payload: Any,
    *,
    source: str = "kalshi",
) -> int:
    """Append new canonical observations and return the number inserted."""
    snapshots = parse_kalshi_market_snapshots(payload, source=source)
    unique_snapshots: dict[tuple[object, ...], dict[str, Any]] = {}
    for snapshot in snapshots:
        key = (snapshot["source"], snapshot["market_ticker"], snapshot["snapshot_timestamp"])
        previous = unique_snapshots.get(key)
        if previous is not None and (
            previous["yes_bid"] != snapshot["yes_bid"]
            or previous["yes_ask"] != snapshot["yes_ask"]
            or previous["no_bid"] != snapshot["no_bid"]
            or previous["no_ask"] != snapshot["no_ask"]
            or previous["event_ticker"] != snapshot["event_ticker"]
            or previous["side"] != snapshot["side"]
        ):
            raise KalshiDataError(
                "payload contains conflicting values for the same kalshi market snapshot"
            )
        unique_snapshots[key] = snapshot

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        _ensure_kalshi_market_snapshots_table(connection)

        for key, snapshot in unique_snapshots.items():
            existing = connection.execute(
                """
                SELECT yes_bid, yes_ask, no_bid, no_ask, event_ticker, side
                FROM bronze.kalshi_market_snapshots
                WHERE source = ? AND market_ticker = ? AND snapshot_timestamp = ?
                """,
                list(key),
            ).fetchone()
            if existing is not None and (
                existing[0] != snapshot["yes_bid"]
                or existing[1] != snapshot["yes_ask"]
                or existing[2] != snapshot["no_bid"]
                or existing[3] != snapshot["no_ask"]
                or existing[4] != snapshot["event_ticker"]
                or existing[5] != snapshot["side"]
            ):
                raise KalshiDataError(
                    "stored snapshot conflicts with the immutable incoming observation"
                )

        inserted = 0
        for snapshot in unique_snapshots.values():
            row = connection.execute(
                """
                INSERT INTO bronze.kalshi_market_snapshots
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING 1
                """,
                [
                    snapshot["source"],
                    snapshot["market_ticker"],
                    snapshot["event_ticker"],
                    snapshot["side"],
                    snapshot["yes_bid"],
                    snapshot["yes_ask"],
                    snapshot["no_bid"],
                    snapshot["no_ask"],
                    snapshot["snapshot_timestamp"],
                    snapshot["source_payload_sha256"],
                ],
            ).fetchone()
            inserted += row is not None
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return inserted


def _ensure_kalshi_market_snapshots_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.kalshi_market_snapshots (
            source VARCHAR NOT NULL,
            market_ticker VARCHAR NOT NULL,
            event_ticker VARCHAR NOT NULL,
            side VARCHAR NOT NULL,
            yes_bid DECIMAL(5,4) NOT NULL CHECK (yes_bid >= 0 AND yes_bid <= 1),
            yes_ask DECIMAL(5,4) NOT NULL CHECK (yes_ask >= 0 AND yes_ask <= 1),
            no_bid DECIMAL(5,4) NOT NULL CHECK (no_bid >= 0 AND no_bid <= 1),
            no_ask DECIMAL(5,4) NOT NULL CHECK (no_ask >= 0 AND no_ask <= 1),
            snapshot_timestamp TIMESTAMPTZ NOT NULL,
            source_payload_sha256 VARCHAR NOT NULL,
            PRIMARY KEY (source, market_ticker, snapshot_timestamp)
        )
        """
    )
