import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.kalshi import KalshiDataError, ingest_kalshi_market_snapshots
from storage import connect_database, initialize_storage


FIXTURE = (
    Path(__file__).parents[3]
    / "unit"
    / "ingestion"
    / "kalshi"
    / "fixtures"
    / "kalshi_market_snapshots.json"
)


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_identical_retry_is_idempotent(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()

    with connect_database(paths["database"]) as connection:
        assert ingest_kalshi_market_snapshots(connection, payload) == 2
        assert ingest_kalshi_market_snapshots(connection, payload) == 0
        count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]

    assert count == 2


def test_new_snapshot_timestamp_coexists_without_overwrite(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()
    later = deepcopy(first)
    later["markets"][0]["updated_time"] = "2026-08-13T23:45:00.000000Z"
    later["markets"][0]["yes_bid_dollars"] = "0.4500"
    del later["markets"][1]

    with connect_database(paths["database"]) as connection:
        assert ingest_kalshi_market_snapshots(connection, first) == 2
        assert ingest_kalshi_market_snapshots(connection, later) == 1
        rows = connection.execute(
            """
            SELECT market_ticker, yes_bid, snapshot_timestamp
            FROM bronze.kalshi_market_snapshots
            WHERE market_ticker = 'KXMLBGAME-26AUG161920SEAHOU-SEA'
            ORDER BY snapshot_timestamp
            """
        ).fetchall()

    assert len(rows) == 2
    assert rows[0][1] == Decimal("0.4400")
    assert rows[1][1] == Decimal("0.4500")


def test_same_snapshot_identity_cannot_replace_an_existing_price(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()
    conflicting = deepcopy(first)
    conflicting["markets"] = [conflicting["markets"][0]]
    conflicting["markets"][0]["yes_bid_dollars"] = "0.5000"

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, first)

        with pytest.raises(KalshiDataError, match="immutable incoming observation"):
            ingest_kalshi_market_snapshots(connection, conflicting)

        stored_price = connection.execute(
            """
            SELECT yes_bid FROM bronze.kalshi_market_snapshots
            WHERE market_ticker = 'KXMLBGAME-26AUG161920SEAHOU-SEA'
            """
        ).fetchone()[0]

    assert stored_price == Decimal("0.4400")


def test_conflicting_duplicate_inside_payload_fails_before_insert(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()
    duplicate_market = deepcopy(payload["markets"][0])
    duplicate_market["yes_bid_dollars"] = "0.5000"
    payload["markets"].append(duplicate_market)

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, {"markets": []})

        with pytest.raises(KalshiDataError, match="conflicting values"):
            ingest_kalshi_market_snapshots(connection, payload)

        count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]

    assert count == 0


def test_raw_payload_hash_is_retained_and_shared_across_rows(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, load_fixture())
        hashes = connection.execute(
            "SELECT DISTINCT source_payload_sha256 FROM bronze.kalshi_market_snapshots"
        ).fetchall()

    assert len(hashes) == 1
    assert len(hashes[0][0]) == 64


def test_does_not_touch_the_sportsbook_odds_table(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, load_fixture())
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
            ).fetchall()
        }

    assert "kalshi_market_snapshots" in tables
    assert "odds_moneyline_snapshots" not in tables
