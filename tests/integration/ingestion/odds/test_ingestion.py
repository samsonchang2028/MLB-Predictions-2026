from copy import deepcopy
from datetime import datetime
import json
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
            SELECT bookmaker, outcome, american_price, snapshot_timestamp, commence_time
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
        datetime(2026, 4, 1, 18),
        datetime(2026, 4, 1, 19),
    }
    assert {row[4] for row in rows} == {
        datetime(2026, 4, 1, 20, 10)
    }


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
