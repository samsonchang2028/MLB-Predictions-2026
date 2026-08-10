"""DATA-016 real-payload contract test.

Guards the game-detail request/response contract against a REAL recorded
``feed/live`` response captured with the production ``GAME_DETAIL_FIELDS``
projection (``tests/fixtures/mlb/game_detail_717408_feed_live.json`` — Phillies
vs Padres, 2023-07-15). Hand-authored fixtures always included stats, so only a
real response can prove the projection actually returns the pitching leaves.
This is the offline check that would have caught the P0 hollow-payload defect.
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb

from ingestion.mlb.game_detail import _validate_payload
from transforms import normalize_silver

_FIXTURE = (
    Path(__file__).parents[3] / "fixtures" / "mlb" / "game_detail_717408_feed_live.json"
)
_GAME_PK = 717408
_HOME_TEAM_ID = 143  # Philadelphia Phillies
_AWAY_TEAM_ID = 135  # San Diego Padres


def _fixture_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def test_real_feed_live_fixture_passes_ingestion_guard() -> None:
    # The production hollow-payload guard must ACCEPT a real completed game
    # (no false rejection) — the mirror of the empty-``stats`` rejection.
    payload = _fixture_bytes()

    assert _validate_payload(payload, _GAME_PK) == payload.decode("utf-8")


def _bronze_with_fixture(connection: duckdb.DuckDBPyConnection) -> None:
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
    game_json = json.dumps(
        {
            "gamePk": _GAME_PK,
            "teams": {
                "home": {"team": {"id": _HOME_TEAM_ID, "name": "Philadelphia Phillies"}},
                "away": {"team": {"id": _AWAY_TEAM_ID, "name": "San Diego Padres"}},
            },
        }
    )
    connection.execute(
        """INSERT INTO bronze.mlb_games VALUES (
               ?, '2023', 'R', '2023-07-15 22:05:00', '2023-07-15',
               ?, ?, 'N', 1, 'Final', 'Final', 'F', 'F', NULL, NULL, NULL,
               'schedule-sha', '2023-07-16 12:00:00', ?
           )""",
        [_GAME_PK, _HOME_TEAM_ID, _AWAY_TEAM_ID, game_json],
    )
    connection.execute(
        "INSERT INTO bronze.mlb_game_detail_payloads VALUES (?, ?, ?, ?)",
        [_GAME_PK, "detail-sha-717408", datetime(2023, 7, 16, 13), _fixture_bytes().decode("utf-8")],
    )


def test_real_fixture_yields_nonnull_pitcher_lines_and_starter_reliever_order() -> None:
    connection = duckdb.connect()
    _bronze_with_fixture(connection)

    counts = normalize_silver(connection)

    # 4 home + 5 away boxscore pitchers in the real feed.
    assert counts["pitcher_appearances"] == 9

    rows = connection.execute(
        """SELECT side, appearance_order, is_actual_starter, pitcher_id,
                  innings_pitched, outs_recorded, earned_runs, hits_allowed,
                  walks, strikeouts, batters_faced
           FROM silver.pitcher_appearances
           WHERE game_pk = ?
           ORDER BY side, appearance_order""",
        [_GAME_PK],
    ).fetchall()

    # Every required stat line is non-null (the columns that were 100% NULL).
    for row in rows:
        for value in row[4:]:
            assert value is not None

    # Exactly one actual starter per side, at appearance_order == 1.
    starters = [r for r in rows if r[2]]
    assert {(r[0], r[1]) for r in starters} == {("away", 1), ("home", 1)}

    # Relievers remain identifiable (order > 1, not the actual starter).
    relievers = [r for r in rows if not r[2]]
    assert all(r[1] > 1 for r in relievers)
    assert len(relievers) == 7

    home = [r for r in rows if r[0] == "home"]
    away = [r for r in rows if r[0] == "away"]

    # Real observed starter identities and lines (Ranger Suarez / Blake Snell).
    assert home[0][3] == 624133
    assert home[0][4] == "6.0" and home[0][5] == 18 and home[0][6] == 3
    assert home[0][7] == 6 and home[0][8] == 3 and home[0][9] == 3 and home[0][10] == 25
    assert away[0][3] == 605483
    assert away[0][4] == "5.0" and away[0][5] == 15 and away[0][6] == 0
    assert away[0][7] == 3 and away[0][8] == 3 and away[0][9] == 7 and away[0][10] == 21
