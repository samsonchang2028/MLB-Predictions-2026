"""Ingest point-in-time MLB schedule responses into local storage."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from storage import connect_database, initialize_storage, write_raw_payload

ScheduleFetcher = Callable[[Mapping[str, object]], bytes]


def ingest_schedule(
    storage_root: str | Path,
    fetch_schedule: ScheduleFetcher,
    *,
    season: int | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    fetched_at: datetime | None = None,
) -> dict[str, object]:
    """Fetch and ingest one MLB schedule response.

    Exactly one query form is accepted: a season, or an inclusive start/end date
    range. The fetcher receives MLB Stats API-style query parameters and must
    return the exact response bytes so the source payload can be retained.
    """
    request = _request_parameters(season, start_date, end_date)
    observed_at = _utc_timestamp(fetched_at or datetime.now(timezone.utc))
    payload = fetch_schedule(request)
    if not isinstance(payload, bytes):
        raise TypeError("fetch_schedule must return the exact response as bytes")

    payload_sha = hashlib.sha256(payload).hexdigest()
    relative_raw_path = Path("mlb") / "schedules" / payload_sha[:2] / f"{payload_sha}.json"
    paths = initialize_storage(storage_root)
    raw_path = _retain_raw_payload(storage_root, relative_raw_path, payload)
    games = _parse_games(payload)

    with connect_database(paths["database"]) as connection:
        _create_tables(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO bronze.mlb_schedule_payloads
                    (payload_sha256, raw_path)
                VALUES (?, ?)
                ON CONFLICT DO NOTHING
                """,
                [payload_sha, relative_raw_path.as_posix()],
            )
            connection.execute(
                """
                INSERT INTO bronze.mlb_schedule_fetches
                    (payload_sha256, fetched_at, request_json)
                VALUES (?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    payload_sha,
                    observed_at,
                    json.dumps(request, sort_keys=True, separators=(",", ":")),
                ],
            )
            for game in games:
                values = _game_values(game, payload_sha, observed_at)
                _reject_equal_time_conflict(connection, values)
                connection.execute(_OBSERVATION_INSERT, values)
                connection.execute(_CANONICAL_UPSERT, values)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    return {
        "payload_sha256": payload_sha,
        "raw_path": raw_path,
        "games_seen": len(games),
    }


def _request_parameters(
    season: int | None, start_date: date | str | None, end_date: date | str | None
) -> dict[str, object]:
    using_range = start_date is not None or end_date is not None
    if season is not None and using_range:
        raise ValueError("choose either season or date range ingestion")
    if season is not None:
        latest_season = datetime.now(timezone.utc).year + 1
        if (
            isinstance(season, bool)
            or not isinstance(season, int)
            or not 1876 <= season <= latest_season
        ):
            raise ValueError("season must be a valid MLB season year")
        return {"sportId": 1, "season": season}
    if start_date is None or end_date is None:
        raise ValueError("provide a season or both start_date and end_date")

    start = _iso_date(start_date, "start_date")
    end = _iso_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must not be after end_date")
    return {"sportId": 1, "startDate": start.isoformat(), "endDate": end.isoformat()}


def _iso_date(value: date | str, name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"{name} must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must use YYYY-MM-DD format") from error


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fetched_at must include a timezone")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _retain_raw_payload(root: str | Path, relative_path: Path, payload: bytes) -> Path:
    try:
        return write_raw_payload(root, relative_path, payload)
    except FileExistsError:
        existing = Path(root) / "raw" / relative_path
        if existing.read_bytes() != payload:
            raise RuntimeError("raw payload hash collision")
        return existing


def _parse_games(payload: bytes) -> list[dict[str, Any]]:
    try:
        response = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MLB schedule response is not valid JSON") from error
    if not isinstance(response, dict) or not isinstance(response.get("dates"), list):
        raise ValueError("MLB schedule response must contain a dates list")

    games: list[dict[str, Any]] = []
    seen_game_pks: set[int] = set()
    for date_entry in response.get("dates", []):
        if not isinstance(date_entry, dict) or not isinstance(date_entry.get("games"), list):
            raise ValueError("each schedule date must contain a games list")
        for game in date_entry["games"]:
            if not isinstance(game, dict):
                raise ValueError("each scheduled game must be an object")
            _validate_game(game)
            game_pk = game["gamePk"]
            if game_pk in seen_game_pks:
                raise ValueError(f"duplicate gamePk {game_pk} within one schedule response")
            seen_game_pks.add(game_pk)
            games.append(game)
    return games


def _validate_game(game: dict[str, Any]) -> None:
    game_pk = game.get("gamePk")
    if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0:
        raise ValueError("every scheduled game must have an integer gamePk")

    game_date = game.get("gameDate")
    if not isinstance(game_date, str):
        raise ValueError(f"gamePk {game_pk} must have a gameDate timestamp")
    try:
        parsed_game_date = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"gamePk {game_pk} has an invalid gameDate timestamp") from error
    if parsed_game_date.tzinfo is None or parsed_game_date.utcoffset() is None:
        raise ValueError(f"gamePk {game_pk} gameDate must include a timezone")

    official_date = game.get("officialDate")
    if (
        not isinstance(official_date, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", official_date) is None
    ):
        raise ValueError(f"gamePk {game_pk} must have an officialDate")
    try:
        date.fromisoformat(official_date)
    except ValueError as error:
        raise ValueError(f"gamePk {game_pk} has an invalid officialDate") from error

    status = game.get("status")
    if not isinstance(status, dict):
        raise ValueError(f"gamePk {game_pk} must have a status object")
    for field in (
        "abstractGameState",
        "detailedState",
        "codedGameState",
        "statusCode",
    ):
        if not isinstance(status.get(field), str) or not status[field]:
            raise ValueError(f"gamePk {game_pk} status must have {field}")

    teams = game.get("teams")
    if not isinstance(teams, dict):
        raise ValueError(f"gamePk {game_pk} must have a teams object")
    _team_id(teams, "home", game_pk)
    _team_id(teams, "away", game_pk)


_GAME_COLUMNS = """
    game_pk, payload_sha256, observed_at, season, game_type, game_date,
    official_date, home_team_id, away_team_id, double_header, game_number,
    abstract_game_state, detailed_state, coded_game_state, status_code,
    reschedule_date, rescheduled_from_date, resume_date, status_json, game_json
"""

_OBSERVATION_INSERT = f"""
    INSERT INTO bronze.mlb_game_observations ({_GAME_COLUMNS})
    VALUES ({", ".join("?" for _ in range(20))})
    ON CONFLICT DO NOTHING
"""

_CANONICAL_UPSERT = f"""
    INSERT INTO bronze.mlb_games ({_GAME_COLUMNS})
    VALUES ({", ".join("?" for _ in range(20))})
    ON CONFLICT (game_pk) DO UPDATE SET
        payload_sha256 = excluded.payload_sha256,
        observed_at = excluded.observed_at,
        season = excluded.season,
        game_type = excluded.game_type,
        game_date = excluded.game_date,
        official_date = excluded.official_date,
        home_team_id = excluded.home_team_id,
        away_team_id = excluded.away_team_id,
        double_header = excluded.double_header,
        game_number = excluded.game_number,
        abstract_game_state = excluded.abstract_game_state,
        detailed_state = excluded.detailed_state,
        coded_game_state = excluded.coded_game_state,
        status_code = excluded.status_code,
        reschedule_date = excluded.reschedule_date,
        rescheduled_from_date = excluded.rescheduled_from_date,
        resume_date = excluded.resume_date,
        status_json = excluded.status_json,
        game_json = excluded.game_json
    WHERE excluded.observed_at > mlb_games.observed_at
"""


def _reject_equal_time_conflict(connection: Any, values: list[object]) -> None:
    existing = connection.execute(
        """
        SELECT game_json
        FROM bronze.mlb_games
        WHERE game_pk = ? AND observed_at = ?
        """,
        [values[0], values[2]],
    ).fetchone()
    if existing is not None and json.loads(existing[0]) != json.loads(values[-1]):
        raise ValueError(
            f"conflicting observations for gamePk {values[0]} at {values[2].isoformat()}Z"
        )


def _game_values(
    game: dict[str, Any], payload_sha: str, observed_at: datetime
) -> list[object]:
    status = game.get("status", {})
    teams = game.get("teams", {})
    if not isinstance(status, dict) or not isinstance(teams, dict):
        raise ValueError(f"gamePk {game['gamePk']} has invalid status or teams")
    return [
        game["gamePk"],
        payload_sha,
        observed_at,
        game.get("season"),
        game.get("gameType"),
        _source_timestamp(game["gameDate"]),
        game.get("officialDate"),
        _team_id(teams, "home", game["gamePk"]),
        _team_id(teams, "away", game["gamePk"]),
        game.get("doubleHeader"),
        game.get("gameNumber"),
        status.get("abstractGameState"),
        status.get("detailedState"),
        status.get("codedGameState"),
        status.get("statusCode"),
        game.get("rescheduleDate"),
        game.get("rescheduledFromDate"),
        game.get("resumeDate"),
        json.dumps(status, sort_keys=True, separators=(",", ":")),
        json.dumps(game, sort_keys=True, separators=(",", ":")),
    ]


def _team_id(teams: dict[str, Any], side: str, game_pk: int) -> int:
    try:
        team_id = teams[side]["team"]["id"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"gamePk {game_pk} is missing the {side} team id") from error
    if isinstance(team_id, bool) or not isinstance(team_id, int):
        raise ValueError(f"gamePk {game_pk} has an invalid {side} team id")
    return team_id


def _source_timestamp(value: str) -> datetime:
    """Return a dependency-free, naive UTC value for DuckDB TIMESTAMP."""
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def _create_tables(connection: Any) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.mlb_schedule_payloads (
            payload_sha256 VARCHAR PRIMARY KEY,
            raw_path VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bronze.mlb_schedule_fetches (
            payload_sha256 VARCHAR NOT NULL,
            fetched_at TIMESTAMP NOT NULL,
            request_json VARCHAR NOT NULL,
            PRIMARY KEY (payload_sha256, fetched_at, request_json)
        );
        CREATE TABLE IF NOT EXISTS bronze.mlb_game_observations (
            game_pk BIGINT NOT NULL,
            payload_sha256 VARCHAR NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            season VARCHAR,
            game_type VARCHAR,
            game_date TIMESTAMP,
            official_date DATE,
            home_team_id BIGINT NOT NULL,
            away_team_id BIGINT NOT NULL,
            double_header VARCHAR,
            game_number INTEGER,
            abstract_game_state VARCHAR,
            detailed_state VARCHAR,
            coded_game_state VARCHAR,
            status_code VARCHAR,
            reschedule_date VARCHAR,
            rescheduled_from_date VARCHAR,
            resume_date VARCHAR,
            status_json JSON NOT NULL,
            game_json JSON NOT NULL,
            PRIMARY KEY (game_pk, payload_sha256, observed_at)
        );
        CREATE TABLE IF NOT EXISTS bronze.mlb_games (
            game_pk BIGINT PRIMARY KEY,
            payload_sha256 VARCHAR NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            season VARCHAR,
            game_type VARCHAR,
            game_date TIMESTAMP,
            official_date DATE,
            home_team_id BIGINT NOT NULL,
            away_team_id BIGINT NOT NULL,
            double_header VARCHAR,
            game_number INTEGER,
            abstract_game_state VARCHAR,
            detailed_state VARCHAR,
            coded_game_state VARCHAR,
            status_code VARCHAR,
            reschedule_date VARCHAR,
            rescheduled_from_date VARCHAR,
            resume_date VARCHAR,
            status_json JSON NOT NULL,
            game_json JSON NOT NULL
        )
        """
    )
