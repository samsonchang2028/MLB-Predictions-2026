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


def test_duplicate_identical_market_within_one_payload_inserts_once(tmp_path: Path) -> None:
    # Not a conflict (values match) -- this is a within-payload duplicate,
    # distinct from the already-covered within-payload *conflict* case.
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()
    payload["markets"] = [payload["markets"][0], deepcopy(payload["markets"][0])]

    with connect_database(paths["database"]) as connection:
        inserted = ingest_kalshi_market_snapshots(connection, payload)
        count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]

    assert inserted == 1
    assert count == 1


def test_malformed_payload_never_opens_a_transaction_or_creates_the_table(
    tmp_path: Path,
) -> None:
    # Validation must happen before BEGIN TRANSACTION -- a broken payload
    # should leave zero trace, not a half-created schema/table.
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        with pytest.raises(KalshiDataError):
            ingest_kalshi_market_snapshots(connection, {"cursor": "abc"})

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
            ).fetchall()
        }

    assert "kalshi_market_snapshots" not in tables


def test_one_conflicting_market_blocks_the_other_valid_market_in_the_same_call(
    tmp_path: Path,
) -> None:
    # Atomicity: a payload with one brand-new valid market and one market
    # that conflicts with an already-stored row must insert NEITHER -- a
    # partial commit would silently accept data the caller never got to see
    # succeed (the call raised).
    paths = initialize_storage(tmp_path / "data")
    first = load_fixture()

    with connect_database(paths["database"]) as connection:
        assert ingest_kalshi_market_snapshots(connection, first) == 2

        mixed = load_fixture()
        # A genuinely new market (new snapshot_timestamp) ...
        mixed["markets"][1]["updated_time"] = "2026-08-13T23:50:00.000000Z"
        # ... alongside a conflicting re-observation of an already-stored key.
        mixed["markets"][0]["yes_bid_dollars"] = "0.9900"

        with pytest.raises(KalshiDataError, match="immutable incoming observation"):
            ingest_kalshi_market_snapshots(connection, mixed)

        count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]
        new_market_rows = connection.execute(
            """
            SELECT count(*) FROM bronze.kalshi_market_snapshots
            WHERE snapshot_timestamp = '2026-08-13 23:50:00'
            """
        ).fetchone()[0]

    # Still just the original 2 rows -- the new-timestamp Houston row from
    # the mixed payload must NOT have snuck in even though its own key had
    # no conflict.
    assert count == 2
    assert new_market_rows == 0


def test_all_fields_round_trip_losslessly_for_both_real_markets(tmp_path: Path) -> None:
    # No-silent-row-loss + full field fidelity against the real captured
    # 2-market fixture, not just the first row / a single column.
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        assert ingest_kalshi_market_snapshots(connection, load_fixture()) == 2
        rows = connection.execute(
            """
            SELECT market_ticker, event_ticker, side, yes_bid, yes_ask, no_bid, no_ask
            FROM bronze.kalshi_market_snapshots
            ORDER BY market_ticker
            """
        ).fetchall()

    assert rows == [
        (
            "KXMLBGAME-26AUG161920SEAHOU-HOU",
            "KXMLBGAME-26AUG161920SEAHOU",
            "Houston",
            Decimal("0.5300"),
            Decimal("0.5500"),
            Decimal("0.4500"),
            Decimal("0.4700"),
        ),
        (
            "KXMLBGAME-26AUG161920SEAHOU-SEA",
            "KXMLBGAME-26AUG161920SEAHOU",
            "Seattle",
            Decimal("0.4400"),
            Decimal("0.4700"),
            Decimal("0.5300"),
            Decimal("0.5600"),
        ),
    ]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P2 known defect (unreachable with real Kalshi payloads observed so far, "
        "which are always 4-decimal-place price strings): a price string with "
        "more than 4 fractional digits passes _price()'s [0,1] bounds check "
        "unchanged, but the bronze.kalshi_market_snapshots.yes_bid column is "
        "DECIMAL(5,4), so DuckDB silently ROUNDS it on insert instead of "
        "raising -- e.g. '0.12345' is stored as 0.1235 with no error and no "
        "trace of the loss. This violates the task's 'store raw price strings "
        "losslessly' requirement and the repo's 'raw API responses are "
        "immutable' rule. Fix: reject values with more than 4 fractional "
        "digits in _price(), or widen/round explicitly with a documented "
        "policy, before this DB write."
    ),
)
def test_price_with_more_than_four_decimal_digits_is_not_silently_rounded(
    tmp_path: Path,
) -> None:
    paths = initialize_storage(tmp_path / "data")
    payload = load_fixture()
    payload["markets"] = [payload["markets"][0]]
    payload["markets"][0]["yes_bid_dollars"] = "0.12345"

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, payload)
        stored = connection.execute(
            "SELECT yes_bid FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]

    assert stored == Decimal("0.12345")
