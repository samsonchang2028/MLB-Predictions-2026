import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.odds import OddsDataError, ingest_the_odds_api_moneylines
from storage import connect_database, initialize_storage


FIXTURE = (
    Path(__file__).parents[3]
    / "unit"
    / "ingestion"
    / "odds"
    / "fixtures"
    / "the_odds_api_moneylines.json"
)


def load_fixture() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_identical_retry_is_idempotent(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()

    with connect_database(paths["database"]) as connection:
        assert ingest_the_odds_api_moneylines(connection, payload) == 4
        assert ingest_the_odds_api_moneylines(connection, payload) == 0
        count = connection.execute(
            "SELECT count(*) FROM bronze.odds_moneyline_snapshots"
        ).fetchone()[0]

    assert count == 4


def test_multiple_books_times_and_prices_coexist_without_overwrite(
    tmp_path: Path,
) -> None:
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()
    later = deepcopy(first)
    later[0]["bookmakers"] = [later[0]["bookmakers"][0]]
    later[0]["bookmakers"][0]["last_update"] = "2026-04-01T19:00:00Z"
    later[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 130

    with connect_database(paths["database"]) as connection:
        assert ingest_the_odds_api_moneylines(connection, first) == 4
        assert ingest_the_odds_api_moneylines(connection, later) == 2
        rows = connection.execute(
            """
            SELECT bookmaker, outcome, american_price, snapshot_timestamp, commence_time,
                   home_team, away_team
            FROM bronze.odds_moneyline_snapshots
            ORDER BY bookmaker, snapshot_timestamp, outcome
            """
        ).fetchall()

    assert len(rows) == 6
    assert {row[0] for row in rows} == {"book_a", "book_b"}
    assert {row[2] for row in rows if row[0] == "book_a" and row[1] == "home"} == {
        125,
        130,
    }
    assert {row[3] for row in rows if row[0] == "book_a"} == {
        datetime(2026, 4, 1, 18, tzinfo=timezone.utc),
        datetime(2026, 4, 1, 19, tzinfo=timezone.utc),
    }
    assert {row[4] for row in rows} == {
        datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc)
    }
    assert {row[5] for row in rows} == {"San Francisco Giants"}
    assert {row[6] for row in rows} == {"Los Angeles Dodgers"}


def test_same_snapshot_identity_cannot_replace_an_existing_price(
    tmp_path: Path,
) -> None:
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()
    conflicting = deepcopy(first)
    conflicting[0]["bookmakers"] = [conflicting[0]["bookmakers"][0]]
    conflicting[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 130

    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, first)

        with pytest.raises(OddsDataError, match="immutable incoming observation"):
            ingest_the_odds_api_moneylines(connection, conflicting)

        stored_price = connection.execute(
            """
            SELECT american_price
            FROM bronze.odds_moneyline_snapshots
            WHERE bookmaker = 'book_a' AND outcome = 'home'
            """
        ).fetchone()[0]

    assert stored_price == 125


def test_timestamp_columns_and_native_values_preserve_timezone_semantics(
    tmp_path: Path,
) -> None:
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, load_fixture())
        column_types = dict(
            connection.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'bronze'
                    AND table_name = 'odds_moneyline_snapshots'
                """
            ).fetchall()
        )
        snapshot_timestamp, commence_time = connection.execute(
            """
            SELECT snapshot_timestamp, commence_time
            FROM bronze.odds_moneyline_snapshots
            WHERE bookmaker = 'book_a' AND outcome = 'home'
            """
        ).fetchone()

    assert column_types["snapshot_timestamp"] == "TIMESTAMP WITH TIME ZONE"
    assert column_types["commence_time"] == "TIMESTAMP WITH TIME ZONE"
    assert snapshot_timestamp.tzinfo is not None
    assert commence_time.tzinfo is not None
    assert snapshot_timestamp == datetime(2026, 4, 1, 18, tzinfo=timezone.utc)
    assert commence_time == datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc)


def test_equivalent_offset_instants_are_an_idempotent_retry(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()
    equivalent = deepcopy(first)
    equivalent[0]["commence_time"] = "2026-04-01T13:10:00-07:00"
    equivalent[0]["bookmakers"][0]["last_update"] = "2026-04-01T11:00:00-07:00"

    with connect_database(paths["database"]) as connection:
        assert ingest_the_odds_api_moneylines(connection, first) == 4
        assert ingest_the_odds_api_moneylines(connection, equivalent) == 0
        count = connection.execute(
            "SELECT count(*) FROM bronze.odds_moneyline_snapshots"
        ).fetchone()[0]

    assert count == 4


def test_non_utc_database_session_does_not_shift_stored_instants(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        connection.execute("SET TimeZone = 'America/Los_Angeles'")
        ingest_the_odds_api_moneylines(connection, load_fixture())
        snapshot_timestamp, commence_time = connection.execute(
            """
            SELECT snapshot_timestamp, commence_time
            FROM bronze.odds_moneyline_snapshots
            WHERE bookmaker = 'book_a' AND outcome = 'home'
            """
        ).fetchone()

    assert snapshot_timestamp.astimezone(timezone.utc) == datetime(
        2026, 4, 1, 18, tzinfo=timezone.utc
    )
    assert commence_time.astimezone(timezone.utc) == datetime(
        2026, 4, 1, 20, 10, tzinfo=timezone.utc
    )


def test_invalid_later_event_leaves_no_partially_inserted_rows(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()
    invalid_event = deepcopy(payload[0])
    invalid_event["id"] = "event-overflow"
    invalid_event["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 2**31
    payload.append(invalid_event)

    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, [])

        with pytest.raises(OddsDataError, match="32-bit American integer price"):
            ingest_the_odds_api_moneylines(connection, payload)

        count = connection.execute(
            "SELECT count(*) FROM bronze.odds_moneyline_snapshots"
        ).fetchone()[0]

        invalid_event["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 130
        assert ingest_the_odds_api_moneylines(connection, payload) == 8
        assert ingest_the_odds_api_moneylines(connection, payload) == 0

    assert count == 0


def test_conflicting_duplicate_inside_payload_fails_before_insert(
    tmp_path: Path,
) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()
    duplicate_market = deepcopy(payload[0]["bookmakers"][0]["markets"][0])
    duplicate_market["outcomes"][0]["price"] = 130
    payload[0]["bookmakers"][0]["markets"].append(duplicate_market)

    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, [])

        with pytest.raises(OddsDataError, match="conflicting values"):
            ingest_the_odds_api_moneylines(connection, payload)

        count = connection.execute(
            "SELECT count(*) FROM bronze.odds_moneyline_snapshots"
        ).fetchone()[0]

    assert count == 0
