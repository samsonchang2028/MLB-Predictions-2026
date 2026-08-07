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
            commence_time TIMESTAMPTZ, home_team VARCHAR, away_team VARCHAR
        )
        """
    )


def _team_name(team_id: int, explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit
    defaults = {
        137: "San Francisco Giants",
        119: "Los Angeles Dodgers",
        147: "New York Yankees",
        111: "Boston Red Sox",
    }
    return defaults.get(team_id, f"Team {team_id}")


def _game(
    connection: duckdb.DuckDBPyConnection,
    game_pk: int,
    game_date: str,
    *,
    home_id: int = 137,
    away_id: int = 119,
    home_name: str | None = None,
    away_name: str | None = None,
    payload_teams: dict[str, object] | None = None,
) -> None:
    if payload_teams is None:
        payload_teams = {
            "home": {
                "team": {"id": home_id, "name": _team_name(home_id, home_name)},
            },
            "away": {
                "team": {"id": away_id, "name": _team_name(away_id, away_name)},
            },
        }
    payload = {
        "gamePk": game_pk,
        "teams": payload_teams,
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
    home_team: str = "San Francisco Giants",
    away_team: str = "Los Angeles Dodgers",
) -> None:
    for outcome, price in (("home", -120), ("away", 110)):
        connection.execute(
            "INSERT INTO bronze.odds_moneyline_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "provider",
                event_id,
                bookmaker,
                outcome,
                price,
                datetime.fromisoformat(snapshot_time.replace("Z", "+00:00")),
                datetime.fromisoformat(commence_time.replace("Z", "+00:00")),
                home_team,
                away_team,
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
    # Same commence + same club names on two game_pks → ambiguous under team match.
    _game(connection, 203, "2026-04-01 21:10:00", home_id=10, away_id=20)
    _game(connection, 204, "2026-04-01 21:10:00", home_id=10, away_id=20)
    _odds(
        connection,
        "ambiguous",
        "2026-04-01T21:10:00Z",
        home_team="Team 10",
        away_team="Team 20",
    )
    _odds(
        connection,
        "missing",
        "2026-04-02T20:10:00Z",
        home_team="Team 1",
        away_team="Team 2",
    )
    # Concurrent slate present, but odds teams match neither loaded game.
    _odds(
        connection,
        "wrong-teams",
        "2026-04-01T20:10:00Z",
        home_team="Team 99",
        away_team="Team 98",
    )

    normalize_silver(connection)
    mappings = connection.execute(
        """
        SELECT source_event_id, mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping ORDER BY source_event_id
        """
    ).fetchall()

    assert mappings == [
        (
            "ambiguous",
            "ambiguous",
            "multiple_exact_commence_and_team_matches",
            None,
            2,
        ),
        ("missing", "unmapped", "no_exact_commence_and_team_match", None, 0),
        ("wrong-teams", "unmapped", "no_exact_commence_and_team_match", None, 0),
    ]


def test_incomplete_concurrent_slate_does_not_attach_unrelated_odds() -> None:
    """Odds for NYY@BOS must not attach to the only loaded SF@LAD at same commence."""
    connection = duckdb.connect()
    _bronze(connection)
    _game(
        connection,
        5010,
        "2026-04-01 20:10:00",
        home_id=137,
        away_id=119,
        home_name="San Francisco Giants",
        away_name="Los Angeles Dodgers",
    )
    _odds(
        connection,
        "nyy-bos",
        "2026-04-01T20:10:00Z",
        home_team="New York Yankees",
        away_team="Boston Red Sox",
    )

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE source_event_id = 'nyy-bos'
        """
    ).fetchone()

    assert mapping == ("unmapped", "no_exact_commence_and_team_match", None, 0)


def test_swapped_home_away_teams_do_not_wrong_attach() -> None:
    """Side-aware identity: matching clubs on the wrong sides must stay unmapped."""
    connection = duckdb.connect()
    _bronze(connection)
    _game(
        connection,
        5020,
        "2026-04-01 20:10:00",
        home_id=137,
        away_id=119,
        home_name="San Francisco Giants",
        away_name="Los Angeles Dodgers",
    )
    _odds(
        connection,
        "swapped",
        "2026-04-01T20:10:00Z",
        home_team="Los Angeles Dodgers",
        away_team="San Francisco Giants",
    )

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE source_event_id = 'swapped'
        """
    ).fetchone()

    assert mapping == ("unmapped", "no_exact_commence_and_team_match", None, 0)


def test_null_provider_team_names_never_commence_only_attach() -> None:
    """Legacy NULL bronze team names must not map via sole commence match."""
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 5030, "2026-04-01 20:10:00")
    commence = datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc)
    snap = datetime(2026, 4, 1, 16, 0, tzinfo=timezone.utc)
    for outcome, price in (("home", -120), ("away", 110)):
        connection.execute(
            "INSERT INTO bronze.odds_moneyline_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "provider",
                "legacy-null-teams",
                "book",
                outcome,
                price,
                snap,
                commence,
                None,
                None,
            ],
        )

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE source_event_id = 'legacy-null-teams'
        """
    ).fetchone()

    assert mapping == ("unmapped", "missing_provider_team_names", None, 0)


def test_mlb_game_missing_payload_team_names_cannot_be_commence_only_attached() -> None:
    """Sole commence candidate without MLB team names must stay unmapped."""
    connection = duckdb.connect()
    _bronze(connection)
    _game(
        connection,
        5040,
        "2026-04-01 20:10:00",
        payload_teams={"home": {"team": {"id": 137}}, "away": {"team": {"id": 119}}},
    )
    _odds(connection, "named-odds", "2026-04-01T20:10:00Z")

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE source_event_id = 'named-odds'
        """
    ).fetchone()

    assert mapping == ("unmapped", "no_exact_commence_and_team_match", None, 0)


def test_team_name_case_and_whitespace_still_map_when_sides_match() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 5050, "2026-04-01 20:10:00")
    _odds(
        connection,
        "casefold",
        "2026-04-01T20:10:00Z",
        home_team="  SAN FRANCISCO GIANTS ",
        away_team="los angeles dodgers",
    )

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE source_event_id = 'casefold'
        """
    ).fetchone()

    assert mapping == ("mapped", "unique_exact_commence_and_team_match", 5050, 1)


def test_team_statistics_extract_only_supported_schedule_fields() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    teams = {
        "home": {
            "team": {"id": 137, "name": "San Francisco Giants"},
            "score": 5,
            "isWinner": True,
            "leagueRecord": {"wins": 3, "losses": 1, "pct": ".750"},
        },
        "away": {
            "team": {"id": 119, "name": "Los Angeles Dodgers"},
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

    # Contract: score / is_winner / league_* are post-game fields (ADR-002), not pregame features.
    assert statistics == [
        (119, "away", 2, False, 2, 2, ".500"),
        (137, "home", 5, True, 3, 1, ".750"),
    ]


def test_payload_team_id_must_match_bronze_team_id() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    teams = {
        "home": {"team": {"id": 999, "name": "San Francisco Giants"}, "score": 1},
        "away": {"team": {"id": 119, "name": "Los Angeles Dodgers"}, "score": 0},
    }
    _game(connection, 305, "2026-04-01 20:10:00", payload_teams=teams)

    with pytest.raises(NormalizationError, match="team.id 999 != bronze team_id 137"):
        normalize_silver(connection)


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
        "home": {"team": {"id": 137, "name": "San Francisco Giants"}, "score": 1},
        "away": {"team": {"id": 119, "name": "Los Angeles Dodgers"}, "score": 0},
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


def test_repeated_normalization_is_deterministic_despite_bronze_insert_order() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    # Insert reverse chronological / reverse pk order to prove ordering is not
    # an artifact of Bronze physical insert order.
    _game(connection, 102, "2026-04-01 22:05:00")
    _game(connection, 101, "2026-04-01 18:05:00")
    _odds(connection, "late", "2026-04-01T22:05:00Z", snapshot_time="2026-04-01T17:00:00Z")
    _odds(connection, "early", "2026-04-01T18:05:00Z", snapshot_time="2026-04-01T16:00:00Z")

    first_counts = normalize_silver(connection)
    first = {
        table: connection.execute(f"SELECT * FROM silver.{table} ORDER BY ALL").fetchall()
        for table in (
            "games",
            "team_game_statistics",
            "pitcher_appearances",
            "odds_snapshots",
            "odds_event_game_mapping",
        )
    }
    second_counts = normalize_silver(connection)
    second = {
        table: connection.execute(f"SELECT * FROM silver.{table} ORDER BY ALL").fetchall()
        for table in first
    }

    assert first_counts == second_counts
    assert first == second
    assert [row[0] for row in first["games"]] == [101, 102]
    assert [row[1] for row in first["odds_event_game_mapping"]] == ["early", "late"]


def test_mapped_events_join_games_one_to_one_and_never_many_to_many() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 601, "2026-04-01 19:10:00", home_id=10, away_id=20)
    _game(connection, 602, "2026-04-01 19:10:00", home_id=30, away_id=40)
    _game(connection, 603, "2026-04-01 22:10:00", home_id=50, away_id=60)
    _odds(
        connection,
        "solo-a",
        "2026-04-01T22:10:00Z",
        home_team="Team 50",
        away_team="Team 60",
    )
    _odds(
        connection,
        "solo-b",
        "2026-04-01T22:10:00Z",
        bookmaker="other",
        home_team="Team 50",
        away_team="Team 60",
    )
    # Concurrent slate: team identity selects the correct game (not commence-only).
    _odds(
        connection,
        "match-a",
        "2026-04-01T19:10:00Z",
        home_team="Team 10",
        away_team="Team 20",
    )
    _odds(
        connection,
        "orphan",
        "2026-04-02T19:10:00Z",
        home_team="Team 10",
        away_team="Team 20",
    )

    normalize_silver(connection)

    mapped_join = connection.execute(
        """
        SELECT m.source_event_id, m.game_pk
        FROM silver.odds_event_game_mapping m
        INNER JOIN silver.games g ON g.game_pk = m.game_pk
        WHERE m.mapping_status = 'mapped'
        ORDER BY m.source_event_id, m.source
        """
    ).fetchall()
    mapped_rows = connection.execute(
        """
        SELECT source_event_id, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        WHERE mapping_status = 'mapped'
        ORDER BY source_event_id
        """
    ).fetchall()
    unresolved = connection.execute(
        """
        SELECT source_event_id, mapping_status, game_pk
        FROM silver.odds_event_game_mapping
        WHERE mapping_status <> 'mapped'
        ORDER BY source_event_id
        """
    ).fetchall()
    events_per_mapped_game = connection.execute(
        """
        SELECT game_pk, count(*)
        FROM silver.odds_event_game_mapping
        WHERE mapping_status = 'mapped'
        GROUP BY game_pk
        ORDER BY game_pk
        """
    ).fetchall()

    assert mapped_rows == [
        ("match-a", 601, 1),
        ("solo-a", 603, 1),
        ("solo-b", 603, 1),
    ]
    assert mapped_join == [("match-a", 601), ("solo-a", 603), ("solo-b", 603)]
    assert len(mapped_join) == len(mapped_rows)
    assert unresolved == [("orphan", "unmapped", None)]
    # Many events may share one game; one event never expands to many games.
    assert events_per_mapped_game == [(601, 1), (603, 2)]


def test_same_team_date_doubleheader_is_not_identified_by_team_date_alone() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    # Classic doubleheader identity: same clubs and official_date, distinct game_pk.
    _game(connection, 701, "2026-04-01 13:05:00", home_id=137, away_id=119)
    _game(connection, 702, "2026-04-01 19:05:00", home_id=137, away_id=119)
    _odds(connection, "game1", "2026-04-01T13:05:00Z")
    # Same calendar day, wrong commence — must stay unmapped (not team/date attach).
    _odds(connection, "wrong-time-same-day", "2026-04-01T16:00:00Z")

    normalize_silver(connection)
    games = connection.execute(
        """
        SELECT game_pk, home_team_id, away_team_id, official_date, game_number
        FROM silver.games ORDER BY game_pk
        """
    ).fetchall()
    mappings = connection.execute(
        """
        SELECT source_event_id, mapping_status, game_pk
        FROM silver.odds_event_game_mapping ORDER BY source_event_id
        """
    ).fetchall()

    assert games == [
        (701, 137, 119, datetime(2026, 4, 1).date(), 2),
        (702, 137, 119, datetime(2026, 4, 1).date(), 1),
    ]
    assert mappings == [
        ("game1", "mapped", 701),
        ("wrong-time-same-day", "unmapped", None),
    ]


def test_identical_commence_doubleheader_stays_ambiguous_not_silently_attached() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    _game(connection, 801, "2026-04-01 18:05:00", home_id=137, away_id=119)
    _game(connection, 802, "2026-04-01 18:05:00", home_id=137, away_id=119)
    _odds(connection, "dh-collision", "2026-04-01T18:05:00Z")

    normalize_silver(connection)
    mapping = connection.execute(
        """
        SELECT mapping_status, mapping_reason, game_pk, candidate_count
        FROM silver.odds_event_game_mapping
        """
    ).fetchone()

    assert mapping == (
        "ambiguous",
        "multiple_exact_commence_and_team_matches",
        None,
        2,
    )


def test_source_timestamps_are_preserved_on_games_stats_and_odds() -> None:
    connection = duckdb.connect()
    _bronze(connection)
    teams = {
        "home": {
            "team": {"id": 137, "name": "San Francisco Giants"},
            "score": 4,
            "isWinner": True,
        },
        "away": {
            "team": {"id": 119, "name": "Los Angeles Dodgers"},
            "score": 1,
            "isWinner": False,
        },
    }
    observed = datetime(2026, 3, 31, 12, 0, 0)
    connection.execute(
        """
        INSERT INTO bronze.mlb_games VALUES (
            901, '2026', 'R', '2026-04-01 20:10:00'::TIMESTAMP, '2026-04-01',
            137, 119, 'N', 1, 'Final', 'Final', 'F', 'F', NULL, NULL, NULL,
            'hash-901', ?, ?
        )
        """,
        [observed, json.dumps({"gamePk": 901, "teams": teams})],
    )
    snapshot = datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc)
    # Non-UTC offset that is the same instant as 20:10Z.
    commence = datetime.fromisoformat("2026-04-01T13:10:00-07:00")
    connection.execute(
        "INSERT INTO bronze.odds_moneyline_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "provider",
            "evt-901",
            "book",
            "home",
            -105,
            snapshot,
            commence,
            "San Francisco Giants",
            "Los Angeles Dodgers",
        ],
    )
    connection.execute(
        "INSERT INTO bronze.odds_moneyline_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "provider",
            "evt-901",
            "book",
            "away",
            -115,
            snapshot,
            commence,
            "San Francisco Giants",
            "Los Angeles Dodgers",
        ],
    )

    normalize_silver(connection)

    game_lineage = connection.execute(
        """
        SELECT source_payload_sha256, source_observed_at, source_game_json
        FROM silver.games WHERE game_pk = 901
        """
    ).fetchone()
    stat_lineage = connection.execute(
        """
        SELECT DISTINCT source_payload_sha256, source_observed_at, game_date
        FROM silver.team_game_statistics WHERE game_pk = 901
        """
    ).fetchall()
    odds_lineage = connection.execute(
        """
        SELECT snapshot_timestamp, commence_time
        FROM silver.odds_snapshots
        WHERE source_event_id = 'evt-901'
        ORDER BY outcome
        """
    ).fetchall()
    mapping = connection.execute(
        """
        SELECT mapping_status, game_pk, commence_time
        FROM silver.odds_event_game_mapping WHERE source_event_id = 'evt-901'
        """
    ).fetchone()

    assert game_lineage[0] == "hash-901"
    assert game_lineage[1] == observed
    payload = game_lineage[2]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["gamePk"] == 901
    assert stat_lineage == [("hash-901", observed, datetime(2026, 4, 1, 20, 10))]
    assert odds_lineage == [
        (snapshot, commence.astimezone(timezone.utc)),
        (snapshot, commence.astimezone(timezone.utc)),
    ]
    assert mapping == ("mapped", 901, commence.astimezone(timezone.utc))


def test_empty_bronze_still_materializes_empty_silver_contracts() -> None:
    connection = duckdb.connect()
    _bronze(connection)

    counts = normalize_silver(connection)

    assert counts == {
        "games": 0,
        "team_game_statistics": 0,
        "pitcher_appearances": 0,
        "odds_snapshots": 0,
        "odds_event_game_mapping": 0,
    }
    for table in counts:
        assert connection.execute(f"SELECT count(*) FROM silver.{table}").fetchone() == (
            0,
        )
