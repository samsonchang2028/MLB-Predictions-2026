"""Parse and persist timestamped moneyline snapshots from The Odds API."""

from datetime import datetime, timezone
from typing import Any

import duckdb

MIN_INTEGER = -(2**31)
MAX_INTEGER = 2**31 - 1


class OddsDataError(ValueError):
    """Raised when an odds payload cannot form a trustworthy snapshot."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OddsDataError(f"{field} is required and must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise OddsDataError(f"{field} must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OddsDataError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise OddsDataError(f"{field} must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise OddsDataError(f"{field} must contain objects")
    return value


def parse_the_odds_api_moneylines(
    payload: Any, *, source: str = "the_odds_api"
) -> list[dict[str, Any]]:
    """Map The Odds API event payloads to canonical home/away observations."""
    source_name = _required_text(source, "source")
    events = _objects(payload, "payload")
    snapshots: list[dict[str, Any]] = []

    for event_index, event in enumerate(events):
        prefix = f"payload[{event_index}]"
        event_id = _required_text(event.get("id"), f"{prefix}.id")
        commence_time = _timestamp(
            event.get("commence_time"), f"{prefix}.commence_time"
        )
        home_team = _required_text(event.get("home_team"), f"{prefix}.home_team")
        away_team = _required_text(event.get("away_team"), f"{prefix}.away_team")
        if home_team == away_team:
            raise OddsDataError(f"{prefix} home_team and away_team must differ")

        bookmakers = _objects(event.get("bookmakers"), f"{prefix}.bookmakers")
        for book_index, book in enumerate(bookmakers):
            book_prefix = f"{prefix}.bookmakers[{book_index}]"
            bookmaker = _required_text(book.get("key"), f"{book_prefix}.key")
            snapshot_timestamp = _timestamp(
                book.get("last_update"), f"{book_prefix}.last_update"
            )
            markets = _objects(book.get("markets"), f"{book_prefix}.markets")

            for market_index, market in enumerate(markets):
                if market.get("key") != "h2h":
                    continue
                market_prefix = f"{book_prefix}.markets[{market_index}]"
                outcomes = _objects(market.get("outcomes"), f"{market_prefix}.outcomes")
                seen_sides: set[str] = set()
                for outcome_index, outcome in enumerate(outcomes):
                    outcome_prefix = f"{market_prefix}.outcomes[{outcome_index}]"
                    team_name = _required_text(
                        outcome.get("name"), f"{outcome_prefix}.name"
                    )
                    if team_name == home_team:
                        side = "home"
                    elif team_name == away_team:
                        side = "away"
                    else:
                        raise OddsDataError(
                            f"{outcome_prefix}.name does not match the event teams"
                        )
                    if side in seen_sides:
                        raise OddsDataError(
                            f"{market_prefix} contains duplicate {side} outcomes"
                        )
                    seen_sides.add(side)

                    price = outcome.get("price")
                    if (
                        isinstance(price, bool)
                        or not isinstance(price, int)
                        or abs(price) < 100
                        or not MIN_INTEGER <= price <= MAX_INTEGER
                    ):
                        raise OddsDataError(
                            f"{outcome_prefix}.price must be a valid 32-bit American integer price"
                        )

                    snapshots.append(
                        {
                            "source": source_name,
                            "source_event_id": event_id,
                            "bookmaker": bookmaker,
                            "outcome": side,
                            "american_price": price,
                            "snapshot_timestamp": snapshot_timestamp,
                            "commence_time": commence_time,
                            "home_team": home_team,
                            "away_team": away_team,
                        }
                    )
                if seen_sides != {"home", "away"}:
                    raise OddsDataError(
                        f"{market_prefix} must contain one home and one away outcome"
                    )

    return snapshots


def ingest_the_odds_api_moneylines(
    connection: duckdb.DuckDBPyConnection,
    payload: Any,
    *,
    source: str = "the_odds_api",
) -> int:
    """Append new canonical observations and return the number inserted."""
    snapshots = parse_the_odds_api_moneylines(payload, source=source)
    unique_snapshots: dict[tuple[object, ...], dict[str, Any]] = {}
    for snapshot in snapshots:
        key = (
            snapshot["source"],
            snapshot["source_event_id"],
            snapshot["bookmaker"],
            snapshot["outcome"],
            snapshot["snapshot_timestamp"],
        )
        previous = unique_snapshots.get(key)
        if previous is not None and (
            previous["american_price"] != snapshot["american_price"]
            or previous["commence_time"] != snapshot["commence_time"]
            or previous["home_team"] != snapshot["home_team"]
            or previous["away_team"] != snapshot["away_team"]
        ):
            raise OddsDataError(
                "payload contains conflicting values for the same odds snapshot"
            )
        unique_snapshots[key] = snapshot

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        _ensure_odds_moneyline_table(connection)

        for key, snapshot in unique_snapshots.items():
            existing = connection.execute(
                """
                SELECT american_price, commence_time, home_team, away_team
                FROM bronze.odds_moneyline_snapshots
                WHERE source = ? AND source_event_id = ? AND bookmaker = ?
                    AND outcome = ? AND snapshot_timestamp = ?
                """,
                list(key),
            ).fetchone()
            if existing is not None and (
                existing[0] != snapshot["american_price"]
                or existing[1] != snapshot["commence_time"]
                or (
                    existing[2] is not None
                    and existing[2] != snapshot["home_team"]
                )
                or (
                    existing[3] is not None
                    and existing[3] != snapshot["away_team"]
                )
            ):
                raise OddsDataError(
                    "stored snapshot conflicts with the immutable incoming observation"
                )

        inserted = 0
        for snapshot in unique_snapshots.values():
            row = connection.execute(
                """
                INSERT INTO bronze.odds_moneyline_snapshots
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING 1
                """,
                [
                    snapshot["source"],
                    snapshot["source_event_id"],
                    snapshot["bookmaker"],
                    snapshot["outcome"],
                    snapshot["american_price"],
                    snapshot["snapshot_timestamp"],
                    snapshot["commence_time"],
                    snapshot["home_team"],
                    snapshot["away_team"],
                ],
            ).fetchone()
            inserted += row is not None
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return inserted


def _ensure_odds_moneyline_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create or migrate bronze odds snapshots; PK unchanged, team names retained."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.odds_moneyline_snapshots (
            source VARCHAR NOT NULL,
            source_event_id VARCHAR NOT NULL,
            bookmaker VARCHAR NOT NULL,
            outcome VARCHAR NOT NULL CHECK (outcome IN ('home', 'away')),
            american_price INTEGER NOT NULL CHECK (
                american_price <= -100 OR american_price >= 100
            ),
            snapshot_timestamp TIMESTAMPTZ NOT NULL,
            commence_time TIMESTAMPTZ NOT NULL,
            home_team VARCHAR NOT NULL,
            away_team VARCHAR NOT NULL,
            PRIMARY KEY (
                source,
                source_event_id,
                bookmaker,
                outcome,
                snapshot_timestamp
            )
        )
        """
    )
    columns = {
        row[0]
        for row in connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'bronze'
                AND table_name = 'odds_moneyline_snapshots'
            """
        ).fetchall()
    }
    # Legacy tables created before team retention: add nullable columns; new
    # ingestions always write names. Existing NULL names stay unmapped in Silver.
    if "home_team" not in columns:
        connection.execute(
            "ALTER TABLE bronze.odds_moneyline_snapshots ADD COLUMN home_team VARCHAR"
        )
    if "away_team" not in columns:
        connection.execute(
            "ALTER TABLE bronze.odds_moneyline_snapshots ADD COLUMN away_team VARCHAR"
        )
