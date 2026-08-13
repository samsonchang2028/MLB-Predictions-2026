"""Build the stable Silver game and moneyline datasets.

Table keys and mapping cardinalities
------------------------------------
``silver.games`` is keyed by ``game_pk``. ``silver.team_game_statistics`` is
keyed by ``(game_pk, team_id)``. ``silver.pitcher_appearances`` is keyed by
``(game_pk, team_id, appearance_order)`` and preserves boxscore pitcher order.
``silver.pitcher_starters`` is keyed by ``(game_pk, team_id)`` and keeps actual
and probable starter identity in separate columns.

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

The ``silver.pitcher_appearances`` this-game pitching-line columns
(``innings_pitched`` through ``home_runs_allowed``) are likewise post-game
outcome facts, not pregame features (ADR-002). The consuming FEAT-002/FEAT-003
builders must shift-before-rolling and must never read a pitcher's current-game
line as a pregame input.
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
        details = _load_game_details(connection)
        team_statistics = _team_statistics(games)
        pitcher_appearances, pitcher_starters = _pitcher_data(games, details)
        mappings = _event_mappings(games, odds)
        _create_tables(connection)
        for table in (
            "odds_event_game_mapping",
            "odds_snapshots",
            "pitcher_starters",
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
        if pitcher_appearances:
            connection.executemany(
                """INSERT INTO silver.pitcher_appearances VALUES (
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                   )""",
                pitcher_appearances,
            )
        if pitcher_starters:
            connection.executemany(
                "INSERT INTO silver.pitcher_starters VALUES (?, ?, ?, ?, ?, ?, ?)",
                pitcher_starters,
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
        _assert_silver_cardinality(
            connection,
            len(games),
            len(team_statistics),
            len(pitcher_appearances),
            len(pitcher_starters),
            len(odds),
            len(mappings),
        )
        _assert_pitcher_join_cardinality(connection)
        _assert_mapped_odds_join_cardinality(connection)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    return {
        "games": len(games),
        "team_game_statistics": len(team_statistics),
        "pitcher_appearances": len(pitcher_appearances),
        "pitcher_starters": len(pitcher_starters),
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


def _load_game_details(connection: Any) -> dict[int, dict[str, Any]]:
    exists = connection.execute(
        """SELECT count(*) FROM information_schema.tables
           WHERE table_schema = 'bronze'
             AND table_name = 'mlb_game_detail_payloads'"""
    ).fetchone()[0]
    if not exists:
        return {}
    rows = connection.execute(
        """SELECT game_pk, payload_sha256, retrieved_at, payload_json
           FROM bronze.mlb_game_detail_payloads ORDER BY game_pk"""
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for game_pk, payload_sha, retrieved_at, payload_json in rows:
        if game_pk in result:
            raise NormalizationError(
                f"bronze game details violate unique game_pk {game_pk}"
            )
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise NormalizationError(
                f"game_pk {game_pk} has invalid detail payload_json"
            ) from error
        if not isinstance(payload, dict) or payload.get("gamePk") != game_pk:
            raise NormalizationError(
                f"game_pk {game_pk} detail payload has mismatched gamePk"
            )
        result[game_pk] = {
            "payload": payload,
            "payload_sha256": payload_sha,
            "retrieved_at": retrieved_at,
        }
    return result


_PITCHING_INT_FIELDS = (
    "outs",
    "battersFaced",
    "numberOfPitches",
    "strikes",
    "balls",
    "hits",
    "runs",
    "earnedRuns",
    "baseOnBalls",
    "strikeOuts",
    "homeRuns",
)


def _pitcher_data(
    games: list[dict[str, Any]], details: dict[int, dict[str, Any]]
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    games_by_pk = {game["values"][0]: game for game in games}
    appearances: list[tuple[object, ...]] = []
    starters: list[tuple[object, ...]] = []
    for game_pk, detail in sorted(details.items()):
        game = games_by_pk.get(game_pk)
        if game is None:
            raise NormalizationError(
                f"game-detail payload references missing bronze game_pk {game_pk}"
            )
        payload = detail["payload"]
        probable_pitchers = _probable_pitchers(payload, game_pk)
        boxscore_teams = _boxscore_teams(payload, game_pk)
        values = game["values"]
        for side, team_id in (("home", values[5]), ("away", values[6])):
            side_boxscore = boxscore_teams.get(side)
            actual_pitcher_id: int | None = None
            if side_boxscore is not None:
                if not isinstance(side_boxscore, dict):
                    raise NormalizationError(
                        f"game_pk {game_pk} {side} boxscore must be an object"
                    )
                _assert_detail_team_id(side_boxscore, team_id, game_pk, side)
                pitcher_ids = _pitcher_ids(side_boxscore, game_pk, side)
                players = side_boxscore.get("players", {})
                if pitcher_ids and not isinstance(players, dict):
                    raise NormalizationError(
                        f"game_pk {game_pk} {side} players must be an object"
                    )
                active_order = 0
                for pitcher_id in pitcher_ids:
                    pitching = _pitching_stats(players, pitcher_id, game_pk, side)
                    if not _has_pitching_activity(pitching):
                        continue
                    active_order += 1
                    if actual_pitcher_id is None:
                        actual_pitcher_id = pitcher_id
                    field_context = f"game_pk {game_pk} pitcher {pitcher_id}"
                    appearances.append(
                        (
                            game_pk,
                            team_id,
                            side,
                            pitcher_id,
                            active_order,
                            active_order == 1,
                            _optional_text(
                                pitching.get("inningsPitched"),
                                f"{field_context} inningsPitched",
                            ),
                            *(
                                _optional_int(
                                    pitching.get(field), f"{field_context} {field}"
                                )
                                for field in _PITCHING_INT_FIELDS
                            ),
                            detail["payload_sha256"],
                            detail["retrieved_at"],
                        )
                    )

            probable_pitcher_id = _probable_pitcher_id(
                probable_pitchers.get(side), game_pk, side
            )
            if actual_pitcher_id is not None or probable_pitcher_id is not None:
                starters.append(
                    (
                        game_pk,
                        team_id,
                        side,
                        actual_pitcher_id,
                        probable_pitcher_id,
                        detail["payload_sha256"],
                        detail["retrieved_at"],
                    )
                )
    return appearances, starters


def _probable_pitchers(payload: dict[str, Any], game_pk: int) -> dict[str, Any]:
    game_data = payload.get("gameData", {})
    if not isinstance(game_data, dict):
        raise NormalizationError(f"game_pk {game_pk} gameData must be an object")
    probable = game_data.get("probablePitchers", {})
    if not isinstance(probable, dict):
        raise NormalizationError(
            f"game_pk {game_pk} probablePitchers must be an object"
        )
    return probable


def _boxscore_teams(payload: dict[str, Any], game_pk: int) -> dict[str, Any]:
    live_data = payload.get("liveData", {})
    if not isinstance(live_data, dict):
        raise NormalizationError(f"game_pk {game_pk} liveData must be an object")
    boxscore = live_data.get("boxscore", {})
    if not isinstance(boxscore, dict):
        raise NormalizationError(f"game_pk {game_pk} boxscore must be an object")
    teams = boxscore.get("teams", {})
    if not isinstance(teams, dict):
        raise NormalizationError(
            f"game_pk {game_pk} boxscore teams must be an object"
        )
    return teams


def _assert_detail_team_id(
    side_boxscore: dict[str, Any], team_id: int, game_pk: int, side: str
) -> None:
    team = side_boxscore.get("team")
    if not isinstance(team, dict):
        raise NormalizationError(
            f"game_pk {game_pk} {side} boxscore is missing team identity"
        )
    detail_team_id = team.get("id")
    if (
        isinstance(detail_team_id, bool)
        or not isinstance(detail_team_id, int)
        or detail_team_id != team_id
    ):
        raise NormalizationError(
            f"game_pk {game_pk} {side} detail team.id {detail_team_id!r} "
            f"!= bronze team_id {team_id}"
        )


def _pitcher_ids(
    side_boxscore: dict[str, Any], game_pk: int, side: str
) -> list[int]:
    values = side_boxscore.get("pitchers", [])
    if not isinstance(values, list):
        raise NormalizationError(
            f"game_pk {game_pk} {side} pitchers must be a list"
        )
    result: list[int] = []
    seen: set[int] = set()
    for pitcher_id in values:
        if (
            isinstance(pitcher_id, bool)
            or not isinstance(pitcher_id, int)
            or pitcher_id <= 0
        ):
            raise NormalizationError(
                f"game_pk {game_pk} {side} pitcher ids must be positive integers"
            )
        if pitcher_id in seen:
            raise NormalizationError(
                f"game_pk {game_pk} {side} repeats pitcher_id {pitcher_id}"
            )
        seen.add(pitcher_id)
        result.append(pitcher_id)
    return result


def _pitching_stats(
    players: dict[str, Any], pitcher_id: int, game_pk: int, side: str
) -> dict[str, Any]:
    player = players.get(f"ID{pitcher_id}")
    if not isinstance(player, dict):
        raise NormalizationError(
            f"game_pk {game_pk} {side} is missing player data for pitcher {pitcher_id}"
        )
    person = player.get("person")
    if not isinstance(person, dict) or person.get("id") != pitcher_id:
        raise NormalizationError(
            f"game_pk {game_pk} {side} pitcher {pitcher_id} has mismatched person.id"
        )
    stats = player.get("stats", {})
    if not isinstance(stats, dict):
        raise NormalizationError(
            f"game_pk {game_pk} pitcher {pitcher_id} stats must be an object"
        )
    pitching = stats.get("pitching", {})
    if not isinstance(pitching, dict):
        raise NormalizationError(
            f"game_pk {game_pk} pitcher {pitcher_id} pitching must be an object"
        )
    return pitching



def _has_pitching_activity(pitching: dict[str, Any]) -> bool:
    innings = pitching.get("inningsPitched")
    if isinstance(innings, str) and innings != "0.0":
        return True
    for key in _PITCHING_INT_FIELDS:
        value = pitching.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return True
    return False

def _probable_pitcher_id(value: object, game_pk: int, side: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise NormalizationError(
            f"game_pk {game_pk} {side} probable pitcher must be an object"
        )
    pitcher_id = value.get("id")
    if isinstance(pitcher_id, bool) or not isinstance(pitcher_id, int) or pitcher_id <= 0:
        raise NormalizationError(
            f"game_pk {game_pk} {side} probable pitcher id must be a positive integer"
        )
    return pitcher_id


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
    # Silver is a deterministic rebuild; recreate the evolved pitcher contract
    # so databases created before DATA-005 migrate atomically.
    connection.execute(
        """CREATE SCHEMA IF NOT EXISTS silver;
           DROP TABLE IF EXISTS silver.pitcher_starters;
           DROP TABLE IF EXISTS silver.pitcher_appearances"""
    )
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
            side VARCHAR NOT NULL CHECK (side IN ('home', 'away')),
            pitcher_id BIGINT NOT NULL,
            appearance_order INTEGER NOT NULL,
            is_actual_starter BOOLEAN NOT NULL,
            -- this-game pitching line: post-game outcome, not a pregame feature
            -- (ADR-002); must be shifted-before-rolling by the consuming FEAT task
            innings_pitched VARCHAR,
            outs_recorded INTEGER,
            batters_faced INTEGER,
            pitches_thrown INTEGER,
            strikes INTEGER,
            balls INTEGER,
            hits_allowed INTEGER,
            runs_allowed INTEGER,
            earned_runs INTEGER,
            walks INTEGER,
            strikeouts INTEGER,
            home_runs_allowed INTEGER,
            source_payload_sha256 VARCHAR NOT NULL,
            source_retrieved_at TIMESTAMP NOT NULL,
            PRIMARY KEY (game_pk, team_id, appearance_order),
            UNIQUE (game_pk, team_id, pitcher_id),
            CHECK ((appearance_order = 1 AND is_actual_starter)
                OR (appearance_order > 1 AND NOT is_actual_starter))
        );
        CREATE TABLE IF NOT EXISTS silver.pitcher_starters (
            game_pk BIGINT NOT NULL,
            team_id BIGINT NOT NULL,
            side VARCHAR NOT NULL CHECK (side IN ('home', 'away')),
            actual_pitcher_id BIGINT,
            probable_pitcher_id BIGINT,
            source_payload_sha256 VARCHAR NOT NULL,
            source_retrieved_at TIMESTAMP NOT NULL,
            PRIMARY KEY (game_pk, team_id),
            CHECK (actual_pitcher_id IS NOT NULL OR probable_pitcher_id IS NOT NULL)
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
    connection: Any,
    games: int,
    team_stats: int,
    pitcher_appearances: int,
    pitcher_starters: int,
    odds: int,
    mappings: int,
) -> None:
    expected = {
        "games": games,
        "team_game_statistics": team_stats,
        "pitcher_appearances": pitcher_appearances,
        "pitcher_starters": pitcher_starters,
        "odds_snapshots": odds,
        "odds_event_game_mapping": mappings,
    }
    for table, count in expected.items():
        actual = connection.execute(f"SELECT count(*) FROM silver.{table}").fetchone()[0]
        if actual != count:
            raise NormalizationError(
                f"silver.{table} cardinality mismatch: expected {count}, found {actual}"
            )


def _assert_pitcher_join_cardinality(connection: Any) -> None:
    for table in ("pitcher_appearances", "pitcher_starters"):
        unmatched = connection.execute(
            f"""
            SELECT pitchers.game_pk, pitchers.team_id, pitchers.side
            FROM silver.{table} AS pitchers
            LEFT JOIN silver.games AS games
              ON games.game_pk = pitchers.game_pk
             AND ((pitchers.side = 'home' AND pitchers.team_id = games.home_team_id)
               OR (pitchers.side = 'away' AND pitchers.team_id = games.away_team_id))
            WHERE games.game_pk IS NULL
            """
        ).fetchall()
        if unmatched:
            raise NormalizationError(
                f"silver.{table} has unmatched game/team identities {unmatched!r}"
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
