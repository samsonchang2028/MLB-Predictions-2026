"""Build the stable Silver game and moneyline datasets.

Table keys and mapping cardinalities
------------------------------------
``silver.games`` is keyed by ``game_pk``. ``silver.team_game_statistics`` is
keyed by ``(game_pk, team_id)``. ``silver.pitcher_appearances`` is keyed by
``(game_pk, team_id, pitcher_id, appearance_order)``. The current schedule
ingestion does not contain pitcher appearance statistics, so that last table is
an intentionally empty, stable contract rather than inferred data.

``silver.odds_snapshots`` retains the Bronze key ``(source, source_event_id,
bookmaker, outcome, snapshot_timestamp)``. ``silver.odds_event_game_mapping``
has one row per ``(source, source_event_id)`` and at most one ``game_pk``.
An odds event is ``mapped`` only when exactly one MLB game matches both its
exact commence instant and provider home/away team names (case-insensitive)
against ``game_json`` team names. Commence-time uniqueness alone never
attaches. Zero matches are ``unmapped``; multiple matches are ``ambiguous``.
Neither attaches a ``game_pk``. This is deliberately stricter than identifying
a game by team/date.

Post-game / not-pregame fields (ADR-002)
----------------------------------------
``silver.team_game_statistics.score``, ``is_winner``, and the Final-row
``league_wins`` / ``league_losses`` / ``league_pct`` values are post-game
outcome and standings fields copied from the schedule payload. They must not
be used as pregame predictive features. They exist for post-hoc labeling and
evaluation only. Pregame feature builders belong in later FEAT tasks and must
use point-in-time-safe inputs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class NormalizationError(ValueError):
    """Raised when Bronze data cannot satisfy the Silver cardinality contract."""


def normalize_silver(connection: Any) -> dict[str, int]:
    """Atomically rebuild deterministic Silver tables from canonical Bronze data."""
    connection.execute("BEGIN TRANSACTION")
    try:
        games = _load_games(connection)
        odds = _load_odds(connection)
        team_statistics = _team_statistics(games)
        mappings = _event_mappings(games, odds)
        _create_tables(connection)
        for table in (
            "odds_event_game_mapping",
            "odds_snapshots",
            "pitcher_appearances",
            "team_game_statistics",
            "games",
        ):
            connection.execute(f"DELETE FROM silver.{table}")

        if games:
            connection.executemany(
                """
                INSERT INTO silver.games VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [game["values"] for game in games],
            )
        if team_statistics:
            connection.executemany(
                "INSERT INTO silver.team_game_statistics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                team_statistics,
            )
        if odds:
            connection.executemany(
                "INSERT INTO silver.odds_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                [row["values"] for row in odds],
            )
        if mappings:
            connection.executemany(
                "INSERT INTO silver.odds_event_game_mapping VALUES (?, ?, ?, ?, ?, ?, ?)",
                mappings,
            )
        _assert_silver_cardinality(connection, len(games), len(team_statistics), len(odds), len(mappings))
        _assert_mapped_odds_join_cardinality(connection)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    return {
        "games": len(games),
        "team_game_statistics": len(team_statistics),
        "pitcher_appearances": 0,
        "odds_snapshots": len(odds),
        "odds_event_game_mapping": len(mappings),
    }


def _load_games(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT game_pk, season, game_type, game_date, official_date,
               home_team_id, away_team_id, double_header, game_number,
               abstract_game_state, detailed_state, coded_game_state,
               status_code, reschedule_date, rescheduled_from_date, resume_date,
               payload_sha256, observed_at, game_json
        FROM bronze.mlb_games
        ORDER BY game_pk
        """
    ).fetchall()
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        game_pk = row[0]
        if game_pk in seen:
            raise NormalizationError(f"bronze.mlb_games violates unique game_pk {game_pk}")
        seen.add(game_pk)
        if row[5] == row[6]:
            raise NormalizationError(f"game_pk {game_pk} has the same home and away team")
        try:
            payload = json.loads(row[18])
        except (TypeError, json.JSONDecodeError) as error:
            raise NormalizationError(f"game_pk {game_pk} has invalid game_json") from error
        if not isinstance(payload, dict):
            raise NormalizationError(f"game_pk {game_pk} game_json must be an object")
        home_name, away_name = _payload_team_names(payload, game_pk)
        result.append(
            {
                "values": row,
                "payload": payload,
                "home_team_name": home_name,
                "away_team_name": away_name,
            }
        )
    return result


def _load_odds(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, source_event_id, bookmaker, outcome, american_price,
               snapshot_timestamp, commence_time, home_team, away_team
        FROM bronze.odds_moneyline_snapshots
        ORDER BY source, source_event_id, bookmaker, outcome, snapshot_timestamp
        """
    ).fetchall()
    seen: set[tuple[object, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (row[0], row[1], row[2], row[3], _utc_instant(row[5]))
        if key in seen:
            raise NormalizationError(f"bronze odds violates snapshot key {key!r}")
        seen.add(key)
        result.append(
            {
                "values": row[:7],
                "event_key": (row[0], row[1]),
                "home_team": row[7],
                "away_team": row[8],
            }
        )
    return result


def _team_statistics(games: list[dict[str, Any]]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    seen: set[tuple[object, object]] = set()
    for game in games:
        values = game["values"]
        game_pk, home_team_id, away_team_id = values[0], values[5], values[6]
        payload_teams = game["payload"].get("teams")
        if not isinstance(payload_teams, dict):
            continue
        for side, team_id in (("home", home_team_id), ("away", away_team_id)):
            source_team = payload_teams.get(side)
            if not isinstance(source_team, dict):
                continue
            _assert_payload_team_id(source_team, team_id, game_pk, side)
            record = source_team.get("leagueRecord")
            record = record if isinstance(record, dict) else {}
            supported = any(
                field in source_team for field in ("score", "isWinner")
            ) or any(field in record for field in ("wins", "losses", "pct"))
            if not supported:
                continue
            key = (game_pk, team_id)
            if key in seen:
                raise NormalizationError(f"duplicate team-game statistics key {key!r}")
            seen.add(key)
            # score / is_winner / league_* are post-game fields (ADR-002), not pregame features.
            rows.append(
                (
                    game_pk,
                    team_id,
                    side,
                    _optional_int(source_team.get("score"), f"game_pk {game_pk} {side} score"),
                    _optional_bool(source_team.get("isWinner"), f"game_pk {game_pk} {side} isWinner"),
                    _optional_int(record.get("wins"), f"game_pk {game_pk} {side} wins"),
                    _optional_int(record.get("losses"), f"game_pk {game_pk} {side} losses"),
                    _optional_text(record.get("pct"), f"game_pk {game_pk} {side} pct"),
                    values[16],
                    values[17],
                    values[3],
                )
            )
    return rows


def _event_mappings(
    games: list[dict[str, Any]], odds: list[dict[str, Any]]
) -> list[tuple[object, ...]]:
    games_by_time: dict[datetime, list[dict[str, Any]]] = {}
    for game in games:
        game_time = game["values"][3]
        if game_time is not None:
            games_by_time.setdefault(_utc_instant(game_time), []).append(game)

    event_meta: dict[tuple[str, str], dict[str, Any]] = {}
    for row in odds:
        event_key = row["event_key"]
        commence = _utc_instant(row["values"][6])
        home_team = row["home_team"]
        away_team = row["away_team"]
        meta = event_meta.get(event_key)
        if meta is None:
            event_meta[event_key] = {
                "commence_times": {commence},
                "home_teams": {home_team},
                "away_teams": {away_team},
            }
            continue
        meta["commence_times"].add(commence)
        meta["home_teams"].add(home_team)
        meta["away_teams"].add(away_team)

    mappings: list[tuple[object, ...]] = []
    for (source, event_id), meta in sorted(event_meta.items()):
        if len(meta["commence_times"]) != 1:
            raise NormalizationError(
                f"odds event {(source, event_id)!r} has multiple commence times"
            )
        if len(meta["home_teams"]) != 1 or len(meta["away_teams"]) != 1:
            raise NormalizationError(
                f"odds event {(source, event_id)!r} has conflicting provider team names"
            )
        commence_time = next(iter(meta["commence_times"]))
        home_team = next(iter(meta["home_teams"]))
        away_team = next(iter(meta["away_teams"]))

        if not _usable_team_name(home_team) or not _usable_team_name(away_team):
            mappings.append(
                (
                    source,
                    event_id,
                    commence_time,
                    "unmapped",
                    "missing_provider_team_names",
                    None,
                    0,
                )
            )
            continue

        home_key = _normalize_team_name(home_team)
        away_key = _normalize_team_name(away_team)
        candidates = sorted(
            game["values"][0]
            for game in games_by_time.get(commence_time, [])
            if game["home_team_name"] == home_key and game["away_team_name"] == away_key
        )
        if len(candidates) == 1:
            status, reason, game_pk = (
                "mapped",
                "unique_exact_commence_and_team_match",
                candidates[0],
            )
        elif not candidates:
            status, reason, game_pk = (
                "unmapped",
                "no_exact_commence_and_team_match",
                None,
            )
        else:
            status, reason, game_pk = (
                "ambiguous",
                "multiple_exact_commence_and_team_matches",
                None,
            )
        mappings.append(
            (source, event_id, commence_time, status, reason, game_pk, len(candidates))
        )
    return mappings


def _payload_team_names(
    payload: dict[str, Any], game_pk: int
) -> tuple[str | None, str | None]:
    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return None, None
    return (
        _side_team_name(teams.get("home"), game_pk, "home"),
        _side_team_name(teams.get("away"), game_pk, "away"),
    )


def _side_team_name(side_obj: object, game_pk: int, side: str) -> str | None:
    if not isinstance(side_obj, dict):
        return None
    team = side_obj.get("team")
    if not isinstance(team, dict):
        return None
    name = team.get("name")
    if name is None:
        return None
    if not isinstance(name, str):
        raise NormalizationError(
            f"game_pk {game_pk} {side} team.name must be text when present"
        )
    if not name.strip():
        return None
    return _normalize_team_name(name)


def _assert_payload_team_id(
    source_team: dict[str, Any], team_id: int, game_pk: int, side: str
) -> None:
    team = source_team.get("team")
    if not isinstance(team, dict) or "id" not in team:
        return
    payload_id = team.get("id")
    if isinstance(payload_id, bool) or not isinstance(payload_id, int):
        raise NormalizationError(
            f"game_pk {game_pk} {side} team.id must be an integer when present"
        )
    if payload_id != team_id:
        raise NormalizationError(
            f"game_pk {game_pk} {side} team.id {payload_id} != bronze team_id {team_id}"
        )


def _usable_team_name(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_team_name(name: str) -> str:
    return name.strip().casefold()


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizationError(f"{field} must be an integer when present")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise NormalizationError(f"{field} must be boolean when present")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NormalizationError(f"{field} must be text when present")
    return value


def _utc_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _create_tables(connection: Any) -> None:
    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS silver;
        CREATE TABLE IF NOT EXISTS silver.games (
            game_pk BIGINT PRIMARY KEY,
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
            source_payload_sha256 VARCHAR NOT NULL,
            source_observed_at TIMESTAMP NOT NULL,
            source_game_json JSON NOT NULL,
            CHECK (home_team_id <> away_team_id)
        );
        CREATE TABLE IF NOT EXISTS silver.team_game_statistics (
            game_pk BIGINT NOT NULL,
            team_id BIGINT NOT NULL,
            side VARCHAR NOT NULL CHECK (side IN ('home', 'away')),
            -- post-game outcome; not a pregame feature (ADR-002)
            score INTEGER,
            -- post-game outcome; not a pregame feature (ADR-002)
            is_winner BOOLEAN,
            -- Final-row league record; not a pregame feature (ADR-002)
            league_wins INTEGER,
            league_losses INTEGER,
            league_pct VARCHAR,
            source_payload_sha256 VARCHAR NOT NULL,
            source_observed_at TIMESTAMP NOT NULL,
            game_date TIMESTAMP NOT NULL,
            PRIMARY KEY (game_pk, team_id)
        );
        CREATE TABLE IF NOT EXISTS silver.pitcher_appearances (
            game_pk BIGINT NOT NULL,
            team_id BIGINT NOT NULL,
            pitcher_id BIGINT NOT NULL,
            appearance_order INTEGER NOT NULL,
            innings_pitched VARCHAR,
            source_payload_sha256 VARCHAR NOT NULL,
            source_observed_at TIMESTAMP NOT NULL,
            PRIMARY KEY (game_pk, team_id, pitcher_id, appearance_order)
        );
        CREATE TABLE IF NOT EXISTS silver.odds_snapshots (
            source VARCHAR NOT NULL,
            source_event_id VARCHAR NOT NULL,
            bookmaker VARCHAR NOT NULL,
            outcome VARCHAR NOT NULL CHECK (outcome IN ('home', 'away')),
            american_price INTEGER NOT NULL,
            snapshot_timestamp TIMESTAMPTZ NOT NULL,
            commence_time TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (source, source_event_id, bookmaker, outcome, snapshot_timestamp)
        );
        CREATE TABLE IF NOT EXISTS silver.odds_event_game_mapping (
            source VARCHAR NOT NULL,
            source_event_id VARCHAR NOT NULL,
            commence_time TIMESTAMPTZ NOT NULL,
            mapping_status VARCHAR NOT NULL CHECK (mapping_status IN ('mapped', 'unmapped', 'ambiguous')),
            mapping_reason VARCHAR NOT NULL,
            game_pk BIGINT,
            candidate_count INTEGER NOT NULL,
            PRIMARY KEY (source, source_event_id),
            CHECK ((mapping_status = 'mapped' AND game_pk IS NOT NULL AND candidate_count = 1)
                OR (mapping_status <> 'mapped' AND game_pk IS NULL))
        )
        """
    )


def _assert_silver_cardinality(
    connection: Any, games: int, team_stats: int, odds: int, mappings: int
) -> None:
    expected = {
        "games": games,
        "team_game_statistics": team_stats,
        "pitcher_appearances": 0,
        "odds_snapshots": odds,
        "odds_event_game_mapping": mappings,
    }
    for table, count in expected.items():
        actual = connection.execute(f"SELECT count(*) FROM silver.{table}").fetchone()[0]
        if actual != count:
            raise NormalizationError(
                f"silver.{table} cardinality mismatch: expected {count}, found {actual}"
            )


def _assert_mapped_odds_join_cardinality(connection: Any) -> None:
    """Mapped events join games 1:1 on game_pk; never expand to many games."""
    bad = connection.execute(
        """
        SELECT m.source, m.source_event_id, count(*) AS game_matches
        FROM silver.odds_event_game_mapping m
        INNER JOIN silver.games g ON g.game_pk = m.game_pk
        WHERE m.mapping_status = 'mapped'
        GROUP BY m.source, m.source_event_id
        HAVING count(*) <> 1
        """
    ).fetchall()
    if bad:
        raise NormalizationError(
            f"mapped odds→games join is not 1:1 for events {bad!r}"
        )
    dangling = connection.execute(
        """
        SELECT source, source_event_id, game_pk
        FROM silver.odds_event_game_mapping
        WHERE mapping_status = 'mapped'
            AND game_pk NOT IN (SELECT game_pk FROM silver.games)
        """
    ).fetchall()
    if dangling:
        raise NormalizationError(
            f"mapped odds reference missing games {dangling!r}"
        )
