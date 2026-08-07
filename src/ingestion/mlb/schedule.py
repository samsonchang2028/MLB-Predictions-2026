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

    # A season-wide schedule legitimately lists one game_pk under more than one
    # date: a postponed game appears on its original date and its makeup appears
    # on the reschedule date (confirmed real example game_pk 634627 in 2021).
    # game_pk is the canonical game identity, and the observation table is keyed
    # (game_pk, payload_sha256, observed_at) so a single response can retain only
    # one observation per game_pk. Repeats within one response are therefore
    # reconciled to a single canonical entry here rather than aborting the build.
    grouped: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for date_entry in response.get("dates", []):
        if not isinstance(date_entry, dict) or not isinstance(date_entry.get("games"), list):
            raise ValueError("each schedule date must contain a games list")
        for game in date_entry["games"]:
            if not isinstance(game, dict):
                raise ValueError("each scheduled game must be an object")
            _validate_game(game)
            game_pk = game["gamePk"]
            if game_pk not in grouped:
                grouped[game_pk] = []
                order.append(game_pk)
            grouped[game_pk].append(game)
    return [_reconcile_repeated_game(game_pk, grouped[game_pk]) for game_pk in order]


# codedGameState lifecycle precedence. A played/Final game supersedes any
# pre-play or postponed/cancelled/suspended placeholder carrying the same
# game_pk. Postponed games can report abstractGameState 'Final' (see DATA-012),
# so precedence is decided on the specific coded lifecycle state rather than the
# abstract one.
_LIFECYCLE_RANK = {
    "C": 0,  # Cancelled
    "D": 0,  # Postponed
    "U": 0,  # Suspended
    "T": 0,  # Suspended, tied
    "S": 1,  # Scheduled
    "P": 2,  # Pre-Game / Warmup
    "I": 3,  # In Progress
    "M": 3,  # Manager Challenge (in progress)
    "O": 4,  # Game Over
    "F": 5,  # Final
}

# Postponement/reschedule and suspension/resume linkage metadata carried forward
# onto the canonical entry so a superseded placeholder's dates are not dropped.
# resumeDate (on the original date) and resumedFromDate (on the resumption date)
# link the two halves of a suspended-then-resumed game (DATA-014).
_RESCHEDULE_FIELDS = (
    "rescheduleDate",
    "rescheduledFromDate",
    "resumeDate",
    "resumedFromDate",
)


def _lifecycle_rank(game: dict[str, Any]) -> int:
    return _LIFECYCLE_RANK.get(game["status"]["codedGameState"], 1)


def _reconcile_repeated_game(
    game_pk: int, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Reconcile one or more schedule entries sharing a game_pk to a single row.

    Precedence: the entry at the most-advanced coded lifecycle stage (Final over
    postponed/scheduled) supplies the canonical outcome fields, and reschedule
    metadata from superseded entries is preserved onto that row. Genuine
    conflicts fail rather than being silently resolved: differing home/away teams
    are an identity conflict, and entries sharing the top lifecycle stage that
    disagree on the game outcome cannot be reconciled.

    A suspended-then-resumed game (DATA-014) lists the same game_pk under both
    its original and resumption dates, and BOTH entries are Final. They describe
    the same completed game and differ only in scheduling/linkage metadata
    (gameDate, resumeDate/resumedFromDate, series counts), so the same-top-stage
    decision compares only the OUTCOME-identifying fields (home/away team id,
    home/away score, winner). Identical outcomes reconcile to one row; a genuine
    outcome disagreement still fails.
    """
    if len(entries) == 1:
        return entries[0]

    # A game_pk denotes exactly one matchup; differing teams is a real identity
    # conflict, not a reschedule.
    identities = {
        (
            _team_id(entry["teams"], "home", game_pk),
            _team_id(entry["teams"], "away", game_pk),
        )
        for entry in entries
    }
    if len(identities) > 1:
        raise ValueError(
            f"gamePk {game_pk} repeats with conflicting home/away teams "
            "within one schedule response"
        )

    top_rank = max(_lifecycle_rank(entry) for entry in entries)
    top_entries = [entry for entry in entries if _lifecycle_rank(entry) == top_rank]

    # Entries sharing the top lifecycle stage are reconcilable only when they
    # agree on the outcome-identifying fields. Scheduling/linkage metadata may
    # legitimately differ between the original and resumption halves of a
    # suspended game, so it is deliberately excluded from this comparison.
    outcomes = {_outcome_identity(entry, game_pk) for entry in top_entries}
    if len(outcomes) > 1:
        raise ValueError(
            f"gamePk {game_pk} has conflicting duplicate entries at the same "
            "lifecycle stage within one schedule response"
        )
    canonical = _canonical_completion_entry(top_entries)
    return _merge_reschedule_metadata(canonical, entries)


# Outcome-identifying fields read from the game teams object. These, not the
# scheduling metadata, decide whether two same-stage entries are the same
# completed game. Score/isWinner are absent before a game is final, so they are
# read defensively; among two Final entries they are populated in real data.
def _outcome_identity(game: dict[str, Any], game_pk: int) -> tuple[object, ...]:
    teams = game["teams"]
    home = teams.get("home", {}) if isinstance(teams, dict) else {}
    away = teams.get("away", {}) if isinstance(teams, dict) else {}
    return (
        _team_id(teams, "home", game_pk),
        _team_id(teams, "away", game_pk),
        home.get("score"),
        away.get("score"),
        home.get("isWinner"),
        away.get("isWinner"),
    )


def _canonical_completion_entry(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministically choose the entry that represents game completion.

    Among identical-outcome entries at the top lifecycle stage the canonical row
    is the completion entry: (1) the one carrying resumedFromDate (the resumption
    half of a suspended game), then (2) the latest gameDate, then (3) the
    earliest encounter order as a stable final tie-break. Point-in-time nuance
    (deferred to a downstream FEAT task, not resolved here): a resumed game's
    result is only known on the resumption gameDate (e.g. 2021-08-31) even though
    its officialDate is the original date (2021-04-11). DATA-014 fixes ingestion
    reconciliation only; temporal feature handling of resumed games is separate.
    """
    best_index, best_entry = 0, entries[0]
    best_key = (best_entry.get("resumedFromDate") is not None, _game_date_instant(best_entry))
    for index in range(1, len(entries)):
        entry = entries[index]
        key = (entry.get("resumedFromDate") is not None, _game_date_instant(entry))
        # Strictly greater key wins; ties keep the earlier encounter order.
        if key > best_key:
            best_index, best_entry, best_key = index, entry, key
    return best_entry


def _game_date_instant(game: dict[str, Any]) -> datetime:
    return (
        datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
    )


def _merge_reschedule_metadata(
    canonical: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    merged = dict(canonical)
    for field in _RESCHEDULE_FIELDS:
        if merged.get(field) is not None:
            continue
        for entry in entries:
            value = entry.get(field)
            if value is not None:
                merged[field] = value
                break
    return merged


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
    -- Equal-time game_json equality is established before this statement.
    WHERE excluded.observed_at > mlb_games.observed_at
       OR (excluded.observed_at = mlb_games.observed_at
           AND excluded.payload_sha256 < mlb_games.payload_sha256)
"""


def _reject_equal_time_conflict(connection: Any, values: list[object]) -> None:
    existing_observations = connection.execute(
        """
        SELECT game_json
        FROM bronze.mlb_game_observations
        WHERE game_pk = ? AND observed_at = ?
        """,
        [values[0], values[2]],
    ).fetchall()
    incoming_game = json.loads(values[-1])
    for (existing_game_json,) in existing_observations:
        if json.loads(existing_game_json) != incoming_game:
            raise ValueError(
                f"conflicting observations for gamePk {values[0]} "
                f"at {values[2].isoformat()}Z"
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
