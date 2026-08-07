import json
from datetime import datetime, timezone

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
            commence_time TIMESTAMPTZ
        )
        """
    )


def _game(
    connection: duckdb.DuckDBPyConnection,
    game_pk: int,
    game_date: str,
    *,
    home_id: int = 137,
    away_id: int = 119,
    payload_teams: dict[str, object] | None = None,
) -> None:
    payload = {
        "gamePk": game_pk,
        "teams": payload_teams
        or {
            "home": {"team": {"id": home_id}},
            "away": {"team": {"id": away_id}},
        },
    }
    connection.execute(
        """
        INSERT INTO bronze.mlb_games VALUES (
            ?, '2026', 'R', ?::TIMESTAMP, '2026-04-01', ?, ?, 'Y', ?,
            'Preview', 'Scheduled', 'S', 'S', NULL, NULL, NULL,
            'payload-hash', '2026-03-31 12:00:00', ?
        )
        """,
        [game_pk, game_date, home_id, away_id, game_pk % 2 + 1, json.dumps(payload)],
    )


def _odds(
    connection: duckdb.DuckDBPyConnection,
    event_id: str,
    commence_time: str,
    *,
    snapshot_time: str = "2026-04-01T16:00:00Z",
    bookmaker: str = "book",
) -> None:
    for outcome, price in (("home", -120), ("away", 110)):
        connection.execute(
            "INSERT INTO bronze.odds_moneyline_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "provider",
                event_id,
                bookmaker,
                outcome,
                price,
                datetime.fromisoformat(snapshot_time.replace("Z", "+00:00")),
                datetime.fromisoformat(commence_time.replace("Z", "+00:00")),
            ],
        )


def test_doubleheaders_map_by_distinct_exact_commence_instants() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 101, "2026-04-01 18:05:00")
    _game(connection, 102, "2026-04-01 22:05:00")
    _odds(connection, "early", "2026-04-01T18:05:00Z")
    _odds(connection, "late", "2026-04-01T22:05:00Z")

    result = normalize_silver(connection)
    mappings = connection.execute(
        """
        SELECT source_event_id, mapping_status, game_pk, candidate_count
        FROM silver.odds_event_game_mapping ORDER BY source_event_id
        """
    ).fetchall()

    assert result["games"] == 2
    assert mappings == [("early", "mapped", 101, 1), ("late", "mapped", 102, 1)]


def test_unmapped_and_ambiguous_events_are_surfaced_without_game_pk() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 201, "2026-04-01 20:10:00", home_id=1, away_id=2)
    _game(connection, 202, "2026-04-01 20:10:00", home_id=3, away_id=4)
    _odds(connection, "ambiguous", "2026-04-01T20:10:00Z")
    _odds(connection, "missing", "2026-04-02T20:10:00Z")

    normalize_silver(connection)
    mappings = connection.execute(
        """
        SELECT source_event_id, mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping ORDER BY source_event_id
        """
    ).fetchall()

    assert mappings == [
        ("ambiguous", "ambiguous", "multiple_exact_commence_time_matches", None, 2),
        ("missing", "unmapped", "no_exact_commence_time_match", None, 0),
    ]


def test_team_statistics_extract_only_supported_schedule_fields() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    teams = {
        "home": {
            "team": {"id": 137},
            "score": 5,
            "isWinner": True,
            "leagueRecord": {"wins": 3, "losses": 1, "pct": ".750"},
        },
        "away": {
            "team": {"id": 119},
            "score": 2,
            "isWinner": False,
            "leagueRecord": {"wins": 2, "losses": 2, "pct": ".500"},
        },
    }
    _game(connection, 301, "2026-04-01 20:10:00", payload_teams=teams)

    normalize_silver(connection)
    statistics = connection.execute(
        """
        SELECT team_id, side, score, is_winner, league_wins, league_losses, league_pct
        FROM silver.team_game_statistics ORDER BY side
        """
    ).fetchall()

    assert statistics == [
        (119, "away", 2, False, 2, 2, ".500"),
        (137, "home", 5, True, 3, 1, ".750"),
    ]


def test_schedule_without_statistics_leaves_explicit_stat_contracts_empty() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 401, "2026-04-01 20:10:00")

    result = normalize_silver(connection)

    assert result["team_game_statistics"] == 0
    assert result["pitcher_appearances"] == 0
    assert connection.execute("SELECT * FROM silver.team_game_statistics").fetchall() == []
    assert connection.execute("SELECT * FROM silver.pitcher_appearances").fetchall() == []


def test_documented_silver_keys_are_database_enforced() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    teams = {
        "home": {"team": {"id": 137}, "score": 1},
        "away": {"team": {"id": 119}, "score": 0},
    }
    _game(connection, 450, "2026-04-01 20:10:00", payload_teams=teams)
    _odds(connection, "keyed", "2026-04-01T20:10:00Z")
    normalize_silver(connection)

    duplicate_statements = (
        "INSERT INTO silver.games SELECT * FROM silver.games LIMIT 1",
        "INSERT INTO silver.team_game_statistics SELECT * FROM silver.team_game_statistics LIMIT 1",
        "INSERT INTO silver.odds_snapshots SELECT * FROM silver.odds_snapshots LIMIT 1",
        "INSERT INTO silver.odds_event_game_mapping SELECT * FROM silver.odds_event_game_mapping LIMIT 1",
    )
    for statement in duplicate_statements:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(statement)

    connection.execute(
        """
        INSERT INTO silver.pitcher_appearances
        VALUES (450, 137, 99, 1, '5.0', 'payload-hash', '2026-03-31 12:00:00')
        """
    )
    with pytest.raises(duckdb.ConstraintException):
        connection.execute(
            """
            INSERT INTO silver.pitcher_appearances
            VALUES (450, 137, 99, 1, '5.0', 'payload-hash', '2026-03-31 12:00:00')
            """
        )


def test_unique_key_violation_rolls_back_and_preserves_previous_silver_rows() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 501, "2026-04-01 20:10:00")
    normalize_silver(connection)
    _game(connection, 501, "2026-04-02 20:10:00")

    with pytest.raises(NormalizationError, match="unique game_pk 501"):
        normalize_silver(connection)

    stored = connection.execute("SELECT game_pk, game_date FROM silver.games").fetchall()
    assert stored == [(501, datetime(2026, 4, 1, 20, 10))]


def test_one_odds_event_with_multiple_commence_times_is_cardinality_failure() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _odds(connection, "changed", "2026-04-01T20:10:00Z", bookmaker="a")
    _odds(connection, "changed", "2026-04-01T21:10:00Z", bookmaker="b")

    with pytest.raises(NormalizationError, match="multiple commence times"):
        normalize_silver(connection)

    assert connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'silver'"
    ).fetchone() == (0,)
