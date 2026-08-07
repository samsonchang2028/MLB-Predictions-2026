import json
from datetime import datetime

import duckdb
import pytest

from transforms import NormalizationError, normalize_silver


def _bronze(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE SCHEMA bronze;
        CREATE TABLE bronze.mlb_games (
            game_pk BIGINT, season VARCHAR, game_type VARCHAR, game_date TIMESTAMP,
            official_date DATE, home_team_id BIGINT, away_team_id BIGINT,
            double_header VARCHAR, game_number INTEGER, abstract_game_state VARCHAR,
            detailed_state VARCHAR, coded_game_state VARCHAR, status_code VARCHAR,
            reschedule_date VARCHAR, rescheduled_from_date VARCHAR, resume_date VARCHAR,
            payload_sha256 VARCHAR, observed_at TIMESTAMP, game_json JSON
        );
        CREATE TABLE bronze.odds_moneyline_snapshots (
            source VARCHAR, source_event_id VARCHAR, bookmaker VARCHAR,
            outcome VARCHAR, american_price INTEGER, snapshot_timestamp TIMESTAMPTZ,
            commence_time TIMESTAMPTZ, home_team VARCHAR, away_team VARCHAR
        );
        CREATE TABLE bronze.mlb_game_detail_payloads (
            game_pk BIGINT, payload_sha256 VARCHAR, retrieved_at TIMESTAMP,
            payload_json JSON
        )
        """
    )


def _game(
    connection: duckdb.DuckDBPyConnection,
    game_pk: int,
    *,
    game_number: int = 1,
    detail: str = "Final",
) -> None:
    game = {
        "gamePk": game_pk,
        "teams": {
            "home": {"team": {"id": 137, "name": "San Francisco Giants"}},
            "away": {"team": {"id": 110, "name": "Baltimore Orioles"}},
        },
    }
    connection.execute(
        """INSERT INTO bronze.mlb_games VALUES (
               ?, '2025', 'R', '2025-04-01 20:10:00', '2025-04-01',
               137, 110, 'Y', ?, 'Final', ?, 'F', 'F', NULL, NULL, NULL,
               'schedule-sha', '2025-04-02 12:00:00', ?
           )""",
        [game_pk, game_number, detail, json.dumps(game)],
    )


def _player(pitcher_id: int, innings: str, pitches: int) -> dict[str, object]:
    return {
        "person": {"id": pitcher_id, "fullName": f"Pitcher {pitcher_id}"},
        "stats": {
            "pitching": {
                "inningsPitched": innings,
                "outs": 3,
                "battersFaced": 5,
                "numberOfPitches": pitches,
                "strikes": pitches - 10,
                "balls": 10,
                "hits": 1,
                "runs": 1,
                "earnedRuns": 1,
                "baseOnBalls": 1,
                "strikeOuts": 2,
                "homeRuns": 0,
            }
        },
    }


def _detail(
    connection: duckdb.DuckDBPyConnection,
    game_pk: int,
    *,
    home_pitchers: tuple[int, ...] = (11, 12),
    away_pitchers: tuple[int, ...] = (21,),
    probable_home: int | None = 99,
    probable_away: int | None = None,
    home_team_id: int = 137,
) -> None:
    probable: dict[str, object] = {}
    if probable_home is not None:
        probable["home"] = {"id": probable_home, "fullName": "Probable Home"}
    if probable_away is not None:
        probable["away"] = {"id": probable_away, "fullName": "Probable Away"}
    teams = {}
    for side, team_id, pitcher_ids in (
        ("home", home_team_id, home_pitchers),
        ("away", 110, away_pitchers),
    ):
        teams[side] = {
            "team": {"id": team_id},
            "pitchers": list(pitcher_ids),
            "players": {
                f"ID{pitcher_id}": _player(
                    pitcher_id, "5.0" if order == 1 else "1.0", 80 if order == 1 else 20
                )
                for order, pitcher_id in enumerate(pitcher_ids, start=1)
            },
        }
    payload = {
        "gamePk": game_pk,
        "gameData": {"probablePitchers": probable},
        "liveData": {"boxscore": {"teams": teams}},
    }
    connection.execute(
        "INSERT INTO bronze.mlb_game_detail_payloads VALUES (?, ?, ?, ?)",
        [game_pk, f"detail-sha-{game_pk}", datetime(2025, 4, 2, 13), json.dumps(payload)],
    )


def test_extracts_actual_starter_change_missing_probable_and_bullpen_order() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 4001)
    _detail(connection, 4001)

    counts = normalize_silver(connection)
    home_appearances = connection.execute(
        """SELECT pitcher_id, appearance_order, is_actual_starter,
                  innings_pitched, pitches_thrown, earned_runs, walks, strikeouts,
                  source_payload_sha256, source_retrieved_at
           FROM silver.pitcher_appearances
           WHERE game_pk = 4001 AND side = 'home'
           ORDER BY appearance_order"""
    ).fetchall()
    starters = connection.execute(
        """SELECT side, team_id, actual_pitcher_id, probable_pitcher_id,
                  source_payload_sha256
           FROM silver.pitcher_starters WHERE game_pk = 4001 ORDER BY side"""
    ).fetchall()

    assert counts["pitcher_appearances"] == 3
    assert counts["pitcher_starters"] == 2
    assert home_appearances == [
        (11, 1, True, "5.0", 80, 1, 1, 2, "detail-sha-4001", datetime(2025, 4, 2, 13)),
        (12, 2, False, "1.0", 20, 1, 1, 2, "detail-sha-4001", datetime(2025, 4, 2, 13)),
    ]
    assert starters == [
        ("away", 110, 21, None, "detail-sha-4001"),
        ("home", 137, 11, 99, "detail-sha-4001"),
    ]


def test_field_filtered_style_dynamic_id_keyed_players_extracts_stats() -> None:
    # Mirrors a feed/live?fields=... response after DATA-005 stopped filtering the
    # dynamically ID-keyed ``players`` object: only whitelisted structural keys
    # survive, but the full players map (keys like "ID660271") is returned and
    # must still yield pitcher stats.
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 8001)
    payload = {
        "gamePk": 8001,
        "gameData": {"probablePitchers": {}},
        "liveData": {"boxscore": {"teams": {
            "home": {
                "team": {"id": 137},
                "pitchers": [660271],
                "players": {"ID660271": _player(660271, "6.0", 95)},
            },
            "away": {
                "team": {"id": 110},
                "pitchers": [592789],
                "players": {"ID592789": _player(592789, "5.1", 88)},
            },
        }}},
    }
    connection.execute(
        "INSERT INTO bronze.mlb_game_detail_payloads VALUES (8001, 'sha-8001', ?, ?)",
        [datetime(2025, 4, 2, 13), json.dumps(payload)],
    )

    counts = normalize_silver(connection)

    assert counts["pitcher_appearances"] == 2
    assert connection.execute(
        """SELECT pitcher_id, innings_pitched, pitches_thrown, earned_runs
           FROM silver.pitcher_appearances WHERE game_pk = 8001 ORDER BY side"""
    ).fetchall() == [
        (592789, "5.1", 88, 1),
        (660271, "6.0", 95, 1),
    ]


def test_same_day_doubleheader_appearance_rows_remain_distinct_by_game_pk() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 5001, game_number=1)
    _game(connection, 5002, game_number=2)
    _detail(connection, 5001, home_pitchers=(31,), away_pitchers=())
    _detail(connection, 5002, home_pitchers=(32,), away_pitchers=())

    normalize_silver(connection)

    assert connection.execute(
        """SELECT game_pk, team_id, pitcher_id, appearance_order
           FROM silver.pitcher_appearances ORDER BY game_pk"""
    ).fetchall() == [(5001, 137, 31, 1), (5002, 137, 32, 1)]


def test_game_without_boxscore_preserves_probable_without_inventing_actual() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 6001, detail="Cancelled")
    payload = {
        "gamePk": 6001,
        "gameData": {"probablePitchers": {"home": {"id": 61}}},
        "liveData": {},
    }
    connection.execute(
        "INSERT INTO bronze.mlb_game_detail_payloads VALUES (6001, 'sha', ?, ?)",
        [datetime(2025, 4, 2, 13), json.dumps(payload)],
    )

    counts = normalize_silver(connection)

    assert counts["pitcher_appearances"] == 0
    assert connection.execute(
        """SELECT team_id, actual_pitcher_id, probable_pitcher_id
           FROM silver.pitcher_starters"""
    ).fetchone() == (137, None, 61)


def test_detail_team_identity_mismatch_is_a_normalization_failure() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 7001)
    _detail(connection, 7001, home_team_id=999)

    with pytest.raises(NormalizationError, match="team.id 999 != bronze team_id 137"):
        normalize_silver(connection)
