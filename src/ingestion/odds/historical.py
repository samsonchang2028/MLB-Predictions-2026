"""Ingest the fixed historical MLB opening-odds archive."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from storage import connect_database, initialize_storage, write_raw_payload

ARCHIVE_SOURCE = "arnavsaraogi_mlb_odds_archive"
ARCHIVE_RELEASE_URL = (
    "https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset"
)
ARCHIVE_ASSET_NAME = "mlb_odds_dataset.json"
PUBLISHED_SHA256 = "3f952fd0bfae9f4f2d17e66692cb936ce6e1a5f6b415318012090c85933b882b"

MIN_INTEGER = -(2**31)
MAX_INTEGER = 2**31 - 1


class HistoricalOddsArchiveError(ValueError):
    """Raised when the fixed archive cannot be retained or parsed safely."""


def parse_historical_odds_archive(payload: bytes) -> list[dict[str, Any]]:
    """Parse archive moneylines without assigning an observation timestamp."""
    archive = _decode_archive(payload)
    records, _ = _parse_archive(archive)
    return records


def ingest_historical_odds_archive(
    storage_root: str | Path, source_path: str | Path
) -> dict[str, object]:
    """Verify, retain, and idempotently ingest the published archive asset."""
    payload = Path(source_path).read_bytes()
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if payload_sha256 != PUBLISHED_SHA256:
        raise HistoricalOddsArchiveError(
            "historical odds archive SHA-256 mismatch: "
            f"expected {PUBLISHED_SHA256}, found {payload_sha256}"
        )

    paths = initialize_storage(storage_root)
    relative_raw_path = (
        Path("odds")
        / "historical_archive"
        / payload_sha256[:2]
        / f"{payload_sha256}.json"
    )
    raw_path = _retain_raw_archive(storage_root, relative_raw_path, payload)

    archive = _decode_archive(payload)
    records, coverage = _parse_archive(archive)
    artifact_values = (
        ARCHIVE_SOURCE,
        ARCHIVE_RELEASE_URL,
        ARCHIVE_ASSET_NAME,
        payload_sha256,
        relative_raw_path.as_posix(),
        coverage["first_archive_date"],
        coverage["last_archive_date"],
        coverage["game_count"],
        len(records),
    )
    record_values = [_record_values(record, payload_sha256) for record in records]

    with connect_database(paths["database"]) as connection:
        _create_tables(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            existing_artifact = connection.execute(
                """
                SELECT source, release_url, asset_name, payload_sha256, raw_path,
                       first_archive_date, last_archive_date, game_count,
                       moneyline_record_count
                FROM bronze.historical_odds_archive_artifacts
                WHERE payload_sha256 = ?
                """,
                [payload_sha256],
            ).fetchone()
            existing_records = connection.execute(
                f"""
                SELECT {_RECORD_COLUMNS}
                FROM bronze.historical_odds_moneylines
                WHERE payload_sha256 = ?
                ORDER BY archive_date, source_game_index, source_line_index
                """,
                [payload_sha256],
            ).fetchall()

            if existing_artifact is not None:
                if existing_artifact != artifact_values or existing_records != record_values:
                    raise HistoricalOddsArchiveError(
                        "stored historical archive conflicts with immutable source data"
                    )
                connection.execute("COMMIT")
                return _result(
                    payload_sha256, raw_path, coverage, len(records), records_inserted=0
                )
            if existing_records:
                raise HistoricalOddsArchiveError(
                    "stored historical archive records have no matching provenance"
                )

            connection.execute(
                """
                INSERT INTO bronze.historical_odds_archive_artifacts
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                artifact_values,
            )
            if record_values:
                _insert_records(connection, record_values)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    return _result(
        payload_sha256, raw_path, coverage, len(records), records_inserted=len(records)
    )


def _decode_archive(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes):
        raise TypeError("historical archive payload must be bytes")
    try:
        archive = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HistoricalOddsArchiveError(
            "historical odds archive must be valid UTF-8 JSON"
        ) from error
    if not isinstance(archive, dict) or not archive:
        raise HistoricalOddsArchiveError(
            "historical odds archive must be a non-empty date object"
        )
    return archive


def _parse_archive(
    archive: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, object]]:
    records: list[dict[str, Any]] = []
    archive_dates: list[date] = []
    game_count = 0

    for date_text, games in archive.items():
        archive_date = _archive_date(date_text)
        archive_dates.append(archive_date)
        game_list = _objects(games, f"archive[{date_text!r}]")
        for game_index, game in enumerate(game_list):
            game_count += 1
            prefix = f"archive[{date_text!r}][{game_index}]"
            game_view = _object(game.get("gameView"), f"{prefix}.gameView")
            odds = _object(game.get("odds"), f"{prefix}.odds")
            moneylines = _objects(odds.get("moneyline"), f"{prefix}.odds.moneyline")
            if not moneylines:
                continue

            start_time = _timestamp(
                game_view.get("startDate"), f"{prefix}.gameView.startDate"
            )
            home_team, home_abbreviation = _team_identity(
                game_view.get("homeTeam"), f"{prefix}.gameView.homeTeam"
            )
            away_team, away_abbreviation = _team_identity(
                game_view.get("awayTeam"), f"{prefix}.gameView.awayTeam"
            )
            if home_team == away_team:
                raise HistoricalOddsArchiveError(
                    f"{prefix} home and away teams must differ"
                )

            seen_sportsbooks: set[str] = set()
            for line_index, line in enumerate(moneylines):
                line_prefix = f"{prefix}.odds.moneyline[{line_index}]"
                sportsbook = _required_text(
                    line.get("sportsbook"), f"{line_prefix}.sportsbook"
                )
                if sportsbook in seen_sportsbooks:
                    raise HistoricalOddsArchiveError(
                        f"{prefix} contains duplicate sportsbook {sportsbook!r}"
                    )
                seen_sportsbooks.add(sportsbook)
                opening = _object(
                    line.get("openingLine"), f"{line_prefix}.openingLine"
                )
                current = _object(
                    line.get("currentLine"), f"{line_prefix}.currentLine"
                )
                records.append(
                    {
                        "archive_date": archive_date,
                        "source_game_index": game_index,
                        "source_line_index": line_index,
                        "game_start_time": start_time,
                        "home_team": home_team,
                        "home_team_abbreviation": home_abbreviation,
                        "away_team": away_team,
                        "away_team_abbreviation": away_abbreviation,
                        "sportsbook": sportsbook,
                        "opening_home_american": _american_odds(
                            opening.get("homeOdds"),
                            f"{line_prefix}.openingLine.homeOdds",
                        ),
                        "opening_away_american": _american_odds(
                            opening.get("awayOdds"),
                            f"{line_prefix}.openingLine.awayOdds",
                        ),
                        "current_home_american": _american_odds(
                            current.get("homeOdds"),
                            f"{line_prefix}.currentLine.homeOdds",
                        ),
                        "current_away_american": _american_odds(
                            current.get("awayOdds"),
                            f"{line_prefix}.currentLine.awayOdds",
                        ),
                    }
                )

    return records, {
        "first_archive_date": min(archive_dates),
        "last_archive_date": max(archive_dates),
        "date_count": len(archive_dates),
        "game_count": game_count,
    }


def _archive_date(value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise HistoricalOddsArchiveError("archive date keys must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise HistoricalOddsArchiveError(
            "archive date keys must use YYYY-MM-DD format"
        ) from error


def _timestamp(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise HistoricalOddsArchiveError(
            f"{field} must be a valid ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalOddsArchiveError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _team_identity(value: object, field: str) -> tuple[str, str]:
    team = _object(value, field)
    return (
        _required_text(team.get("fullName"), f"{field}.fullName"),
        _required_text(team.get("shortName"), f"{field}.shortName"),
    )


def _american_odds(value: object, field: str) -> int | None:
    # The published source uses zero as an unavailable-price sentinel.
    if value is None or value == 0:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or abs(value) < 100
        or not MIN_INTEGER <= value <= MAX_INTEGER
    ):
        raise HistoricalOddsArchiveError(
            f"{field} must be zero/unavailable or a valid 32-bit American integer price"
        )
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalOddsArchiveError(
            f"{field} is required and must be a non-empty string"
        )
    return value.strip()


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HistoricalOddsArchiveError(f"{field} must be an object")
    return value


def _objects(value: object, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise HistoricalOddsArchiveError(f"{field} must be a list of objects")
    return value


def _retain_raw_archive(root: str | Path, relative_path: Path, payload: bytes) -> Path:
    try:
        return write_raw_payload(root, relative_path, payload)
    except FileExistsError:
        existing = Path(root) / "raw" / relative_path
        if hashlib.sha256(existing.read_bytes()).hexdigest() != PUBLISHED_SHA256:
            raise HistoricalOddsArchiveError(
                "retained historical odds archive conflicts with published checksum"
            )
        return existing


def _record_values(
    record: dict[str, Any], payload_sha256: str
) -> tuple[object, ...]:
    return (
        payload_sha256,
        record["archive_date"],
        record["source_game_index"],
        record["source_line_index"],
        record["game_start_time"],
        record["home_team"],
        record["home_team_abbreviation"],
        record["away_team"],
        record["away_team_abbreviation"],
        record["sportsbook"],
        record["opening_home_american"],
        record["opening_away_american"],
        record["current_home_american"],
        record["current_away_american"],
    )


def _result(
    payload_sha256: str,
    raw_path: Path,
    coverage: dict[str, object],
    records_seen: int,
    *,
    records_inserted: int,
) -> dict[str, object]:
    return {
        "payload_sha256": payload_sha256,
        "raw_path": raw_path,
        **coverage,
        "records_seen": records_seen,
        "records_inserted": records_inserted,
    }


_RECORD_COLUMN_NAMES = (
    "payload_sha256",
    "archive_date",
    "source_game_index",
    "source_line_index",
    "game_start_time",
    "home_team",
    "home_team_abbreviation",
    "away_team",
    "away_team_abbreviation",
    "sportsbook",
    "opening_home_american",
    "opening_away_american",
    "current_home_american",
    "current_away_american",
)
_RECORD_COLUMNS = ", ".join(_RECORD_COLUMN_NAMES)


def _insert_records(connection: Any, rows: list[tuple[object, ...]]) -> None:
    bound_rows = [dict(zip(_RECORD_COLUMN_NAMES, row, strict=True)) for row in rows]
    connection.execute(
        f"""
        INSERT INTO bronze.historical_odds_moneylines ({_RECORD_COLUMNS})
        SELECT {_RECORD_COLUMNS}
        FROM (SELECT unnest(?, recursive := true))
        """,
        [bound_rows],
    )


def _create_tables(connection: Any) -> None:
    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS bronze;
        CREATE TABLE IF NOT EXISTS bronze.historical_odds_archive_artifacts (
            source VARCHAR NOT NULL,
            release_url VARCHAR NOT NULL,
            asset_name VARCHAR NOT NULL,
            payload_sha256 VARCHAR PRIMARY KEY,
            raw_path VARCHAR NOT NULL UNIQUE,
            first_archive_date DATE NOT NULL,
            last_archive_date DATE NOT NULL,
            game_count INTEGER NOT NULL CHECK (game_count >= 0),
            moneyline_record_count INTEGER NOT NULL CHECK (moneyline_record_count >= 0)
        );
        CREATE TABLE IF NOT EXISTS bronze.historical_odds_moneylines (
            payload_sha256 VARCHAR NOT NULL,
            archive_date DATE NOT NULL,
            source_game_index INTEGER NOT NULL CHECK (source_game_index >= 0),
            source_line_index INTEGER NOT NULL CHECK (source_line_index >= 0),
            game_start_time TIMESTAMPTZ NOT NULL,
            home_team VARCHAR NOT NULL,
            home_team_abbreviation VARCHAR NOT NULL,
            away_team VARCHAR NOT NULL,
            away_team_abbreviation VARCHAR NOT NULL,
            sportsbook VARCHAR NOT NULL,
            opening_home_american INTEGER CHECK (
                opening_home_american IS NULL
                OR opening_home_american <= -100 OR opening_home_american >= 100
            ),
            opening_away_american INTEGER CHECK (
                opening_away_american IS NULL
                OR opening_away_american <= -100 OR opening_away_american >= 100
            ),
            -- Post-hoc archive value; not a timestamp-valid live snapshot.
            current_home_american INTEGER CHECK (
                current_home_american IS NULL
                OR current_home_american <= -100 OR current_home_american >= 100
            ),
            current_away_american INTEGER CHECK (
                current_away_american IS NULL
                OR current_away_american <= -100 OR current_away_american >= 100
            ),
            PRIMARY KEY (
                payload_sha256, archive_date, source_game_index, source_line_index
            )
        )
        """
    )
