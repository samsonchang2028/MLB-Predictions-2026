"""Integration tests for DATA-009: odds archive -> game_pk mapping over DuckDB.

Builds real ``silver.games`` via the MLB schedule ingestion + normalization
code, plus a small DATA-008-shaped historical odds Bronze fixture, then runs the
end-to-end ``validate_odds_archive`` path (loaders + mapping + checks + coverage).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ingestion.mlb import ingest_schedule
from ingestion.odds import PUBLISHED_SHA256
from storage import connect_database, initialize_storage
from transforms import normalize_silver
from validation.odds_mapping import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    load_archive_records,
    load_game_candidates,
    validate_odds_archive,
    write_coverage_report,
)

_OBSERVED_AT = datetime(2023, 3, 1, 12, tzinfo=timezone.utc)


def _team(team_id: int, name: str) -> dict:
    return {"team": {"id": team_id, "name": name}, "score": 1, "isWinner": True}


def _game(
    game_pk: int, official_date: str, hour: int, home_id: int, home: str,
    away_id: int, away: str, *, double_header: str = "N", game_number: int = 1,
) -> dict:
    home_side = _team(home_id, home)
    away_side = dict(_team(away_id, away), isWinner=False)
    return {
        "gamePk": game_pk,
        "season": "2023",
        "gameType": "R",
        "gameDate": f"{official_date}T{hour:02d}:10:00Z",
        "officialDate": official_date,
        "doubleHeader": double_header,
        "gameNumber": game_number,
        "teams": {"home": home_side, "away": away_side},
        "status": {
            "abstractGameState": "Final", "detailedState": "Final",
            "codedGameState": "F", "statusCode": "F",
        },
    }


def _schedule_payload() -> bytes:
    games = [
        _game(500, "2023-05-01", 23, 147, "New York Yankees", 111, "Boston Red Sox"),
        # Same-day doubleheader, same matchup: distinguishable only by start time.
        _game(600, "2023-07-04", 17, 112, "Chicago Cubs", 113, "Cincinnati Reds",
              double_header="S", game_number=1),
        _game(601, "2023-07-04", 23, 112, "Chicago Cubs", 113, "Cincinnati Reds",
              double_header="S", game_number=2),
    ]
    return json.dumps({"dates": [{"date": "2023-01-01", "games": games}]}).encode()


def _create_historical_tables(connection) -> None:
    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS bronze;
        CREATE TABLE IF NOT EXISTS bronze.historical_odds_archive_artifacts (
            source VARCHAR NOT NULL, release_url VARCHAR NOT NULL,
            asset_name VARCHAR NOT NULL, payload_sha256 VARCHAR PRIMARY KEY,
            raw_path VARCHAR NOT NULL UNIQUE, first_archive_date DATE NOT NULL,
            last_archive_date DATE NOT NULL, game_count INTEGER NOT NULL,
            moneyline_record_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bronze.historical_odds_moneylines (
            payload_sha256 VARCHAR NOT NULL, archive_date DATE NOT NULL,
            source_game_index INTEGER NOT NULL, source_line_index INTEGER NOT NULL,
            game_start_time TIMESTAMPTZ NOT NULL, home_team VARCHAR NOT NULL,
            home_team_abbreviation VARCHAR NOT NULL, away_team VARCHAR NOT NULL,
            away_team_abbreviation VARCHAR NOT NULL, sportsbook VARCHAR NOT NULL,
            opening_home_american INTEGER, opening_away_american INTEGER,
            current_home_american INTEGER, current_away_american INTEGER,
            PRIMARY KEY (payload_sha256, archive_date, source_game_index, source_line_index)
        )
        """
    )


def _line(
    archive_date: str, gi: int, li: int, start: str, home: str, ha: str,
    away: str, aa: str, sportsbook: str, oh=-120, oa=110, ch=-115, ca=105,
) -> tuple:
    return (
        PUBLISHED_SHA256, date.fromisoformat(archive_date), gi, li,
        datetime.fromisoformat(start), home, ha, away, aa, sportsbook, oh, oa, ch, ca,
    )


def _insert_archive(connection) -> None:
    rows = [
        # MATCHED single game (two sportsbooks).
        _line("2023-05-01", 0, 0, "2023-05-01T23:10:00+00:00",
              "New York Yankees", "NYY", "Boston Red Sox", "BOS", "DraftKings"),
        _line("2023-05-01", 0, 1, "2023-05-01T23:10:00+00:00",
              "New York Yankees", "NYY", "Boston Red Sox", "BOS", "FanDuel"),
        # Doubleheader game 2 resolved by exact start time -> MATCHED (601).
        _line("2023-07-04", 0, 0, "2023-07-04T23:10:00+00:00",
              "Chicago Cubs", "CHC", "Cincinnati Reds", "CIN", "DraftKings"),
        # Doubleheader whose start time matches neither scheduled game -> AMBIGUOUS.
        _line("2023-07-04", 1, 0, "2023-07-04T20:00:00+00:00",
              "Chicago Cubs", "CHC", "Cincinnati Reds", "CIN", "DraftKings"),
        # No MLB candidate on that date -> UNMATCHED.
        _line("2023-05-02", 0, 0, "2023-05-02T02:10:00+00:00",
              "Los Angeles Dodgers", "LAD", "San Francisco Giants", "SFG", "DraftKings"),
    ]
    connection.executemany(
        """INSERT INTO bronze.historical_odds_moneylines VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    connection.execute(
        """INSERT INTO bronze.historical_odds_archive_artifacts VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            "arnavsaraogi_mlb_odds_archive", "https://example/release", "mlb_odds_dataset.json",
            PUBLISHED_SHA256, "odds/historical_archive/3f/archive.json",
            date(2023, 5, 1), date(2023, 7, 4), 4, len(rows),
        ],
    )


def _build(root: Path):
    paths = initialize_storage(root)
    ingest_schedule(root, lambda _: _schedule_payload(), season=2023, fetched_at=_OBSERVED_AT)
    with connect_database(paths["database"]) as connection:
        # normalize_silver reads the live-odds Bronze table (DATA-003); this
        # fixture has no live snapshots, so provide an empty one.
        connection.execute(
            """
            CREATE SCHEMA IF NOT EXISTS bronze;
            CREATE TABLE IF NOT EXISTS bronze.odds_moneyline_snapshots (
                source VARCHAR NOT NULL, source_event_id VARCHAR NOT NULL,
                bookmaker VARCHAR NOT NULL, outcome VARCHAR NOT NULL,
                american_price INTEGER NOT NULL, snapshot_timestamp TIMESTAMPTZ NOT NULL,
                commence_time TIMESTAMPTZ NOT NULL, home_team VARCHAR, away_team VARCHAR
            )
            """
        )
        normalize_silver(connection)
        _create_historical_tables(connection)
        _insert_archive(connection)
    return paths["database"]


def _by_event(mappings) -> dict:
    return {m.event_id: m for m in mappings}


def test_loaders_read_archive_and_candidates(tmp_path: Path) -> None:
    database = _build(tmp_path / "data")
    with connect_database(database) as connection:
        records = load_archive_records(connection)
        candidates = load_game_candidates(connection)
    assert len(records) == 5
    assert {c.game_pk for c in candidates} == {500, 600, 601}
    yankees = next(c for c in candidates if c.game_pk == 500)
    assert yankees.home_team_norm == "new york yankees"
    assert yankees.away_team_norm == "boston red sox"


def test_end_to_end_mapping_statuses(tmp_path: Path) -> None:
    database = _build(tmp_path / "data")
    with connect_database(database) as connection:
        result = validate_odds_archive(connection, storage_root=tmp_path / "data")

    mappings = _by_event(result["mappings"])

    matched = mappings[(date(2023, 5, 1), 0)]
    assert matched.status == MATCHED and matched.game_pk == 500

    dh_matched = mappings[(date(2023, 7, 4), 0)]
    assert dh_matched.status == MATCHED
    assert dh_matched.game_pk == 601  # resolved by exact start time, never guessed
    assert dh_matched.resolved_by == "start_time_unique"
    assert dh_matched.candidate_game_pks == (600, 601)

    ambiguous = mappings[(date(2023, 7, 4), 1)]
    assert ambiguous.status == AMBIGUOUS and ambiguous.game_pk is None

    unmatched = mappings[(date(2023, 5, 2), 0)]
    assert unmatched.status == UNMATCHED and unmatched.game_pk is None

    # Merge-blocking mapping-integrity checks pass on this fixture.
    by_check = {r.check: r for r in result["results"]}
    for name in (
        "odds_archive.home_away_orientation",
        "odds_archive.mapped_records_resolve",
        "odds_archive.no_ambiguous_attachment",
        "odds_archive.moneylines_parse",
        "odds_archive.sportsbook_identity_preserved",
        "odds_archive.team_normalization_deterministic",
        "odds_archive.matched_opening_odds_present",
        "odds_archive.published_sha256_recorded",
    ):
        assert by_check[name].status == "PASS", (name, by_check[name].message)


def test_coverage_report_written_and_structured(tmp_path: Path) -> None:
    database = _build(tmp_path / "data")
    with connect_database(database) as connection:
        result = validate_odds_archive(connection, storage_root=tmp_path / "data")
    coverage = result["coverage"]

    assert coverage["totals"]["events"] == 4
    assert coverage["totals"][MATCHED] == 2
    assert coverage["totals"][AMBIGUOUS] == 1
    assert coverage["totals"][UNMATCHED] == 1
    assert coverage["by_season"]["2023"]["events"] == 4
    assert coverage["by_date"]["2023-05-01"][MATCHED] == 1
    assert coverage["by_sportsbook"]["DraftKings"]["lines"] == 4
    assert coverage["by_sportsbook"]["FanDuel"]["lines"] == 1

    out = write_coverage_report(coverage, tmp_path / "coverage.json")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded == coverage  # round-trips deterministically


def test_ambiguous_and_unmatched_never_carry_game_pk(tmp_path: Path) -> None:
    database = _build(tmp_path / "data")
    with connect_database(database) as connection:
        result = validate_odds_archive(connection, storage_root=tmp_path / "data")
    for mapping in result["mappings"]:
        if mapping.status in (AMBIGUOUS, UNMATCHED):
            assert mapping.game_pk is None
        if mapping.status == MATCHED:
            assert mapping.game_pk is not None
