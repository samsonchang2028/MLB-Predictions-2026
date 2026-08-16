import json
from copy import deepcopy
from datetime import datetime, timezone
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


def test_pipe_006_matching_fields_round_trip(tmp_path: Path) -> None:
    """occurrence_datetime/title/no_sub_title persist -- PIPE-006's read path."""
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        ingest_kalshi_market_snapshots(connection, load_fixture())
        rows = connection.execute(
            """
            SELECT market_ticker, no_sub_title, title, occurrence_datetime
            FROM bronze.kalshi_market_snapshots
            ORDER BY market_ticker
            """
        ).fetchall()

    assert rows == [
        (
            "KXMLBGAME-26AUG161920SEAHOU-HOU",
            "Seattle",
            "Seattle vs Houston Winner?",
            datetime(2026, 8, 17, 2, 20, tzinfo=timezone.utc),
        ),
        (
            "KXMLBGAME-26AUG161920SEAHOU-SEA",
            "Houston",
            "Seattle vs Houston Winner?",
            datetime(2026, 8, 17, 2, 20, tzinfo=timezone.utc),
        ),
    ]


def test_pre_pipe_006_table_shape_is_migrated_additively(tmp_path: Path) -> None:
    """A DB created before PIPE-006 (no matching-input columns) still ingests.

    Simulates the pre-existing on-disk table shape DATA-022 originally
    created, then confirms a fresh ingest call transparently adds the new
    nullable columns via ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` rather
    than failing or requiring a manual migration.
    """
    paths = initialize_storage(tmp_path / "data")
    with connect_database(paths["database"]) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        connection.execute(
            """
            CREATE TABLE bronze.kalshi_market_snapshots (
                source VARCHAR NOT NULL,
                market_ticker VARCHAR NOT NULL,
                event_ticker VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                yes_bid DECIMAL(5,4) NOT NULL CHECK (yes_bid >= 0 AND yes_bid <= 1),
                yes_ask DECIMAL(5,4) NOT NULL CHECK (yes_ask >= 0 AND yes_ask <= 1),
                no_bid DECIMAL(5,4) NOT NULL CHECK (no_bid >= 0 AND no_bid <= 1),
                no_ask DECIMAL(5,4) NOT NULL CHECK (no_ask >= 0 AND no_ask <= 1),
                snapshot_timestamp TIMESTAMPTZ NOT NULL,
                source_payload_sha256 VARCHAR NOT NULL,
                PRIMARY KEY (source, market_ticker, snapshot_timestamp)
            )
            """
        )

        assert ingest_kalshi_market_snapshots(connection, load_fixture()) == 2
        no_sub_titles = {
            row[0]
            for row in connection.execute(
                "SELECT no_sub_title FROM bronze.kalshi_market_snapshots"
            ).fetchall()
        }

    assert no_sub_titles == {"Seattle", "Houston"}


def test_pre_pipe_006_existing_row_survives_migration_without_data_loss(
    tmp_path: Path,
) -> None:
    """The real-world scenario: an on-disk `mlb.duckdb` from BEFORE PIPE-006,
    already holding rows in the old (pre-migration) column shape.

    `test_pre_pipe_006_table_shape_is_migrated_additively` above only proves
    the OLD table shape can be created and a fresh ingest succeeds against an
    empty table -- it never puts a row in the table before migrating, so it
    cannot detect data loss. This test inserts a genuine old-shape row FIRST,
    then migrates, and asserts that row's original columns are unchanged and
    its new columns are NULL (not dropped, not backfilled with wrong data),
    while a fresh ingest of new-shape data lands correctly alongside it.
    """
    paths = initialize_storage(tmp_path / "data")
    with connect_database(paths["database"]) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        connection.execute(
            """
            CREATE TABLE bronze.kalshi_market_snapshots (
                source VARCHAR NOT NULL,
                market_ticker VARCHAR NOT NULL,
                event_ticker VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                yes_bid DECIMAL(5,4) NOT NULL CHECK (yes_bid >= 0 AND yes_bid <= 1),
                yes_ask DECIMAL(5,4) NOT NULL CHECK (yes_ask >= 0 AND yes_ask <= 1),
                no_bid DECIMAL(5,4) NOT NULL CHECK (no_bid >= 0 AND no_bid <= 1),
                no_ask DECIMAL(5,4) NOT NULL CHECK (no_ask >= 0 AND no_ask <= 1),
                snapshot_timestamp TIMESTAMPTZ NOT NULL,
                source_payload_sha256 VARCHAR NOT NULL,
                PRIMARY KEY (source, market_ticker, snapshot_timestamp)
            )
            """
        )
        old_snapshot_timestamp = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        connection.execute(
            """
            INSERT INTO bronze.kalshi_market_snapshots
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "kalshi",
                "OLD-TICKER-PRE-MIGRATION",
                "OLD-EVENT-PRE-MIGRATION",
                "OldSide",
                Decimal("0.5000"),
                Decimal("0.5500"),
                Decimal("0.4500"),
                Decimal("0.5000"),
                old_snapshot_timestamp,
                "old-payload-hash",
            ],
        )

        # A fresh, real ingest call is what actually triggers the additive
        # migration (ALTER TABLE ... ADD COLUMN IF NOT EXISTS).
        assert ingest_kalshi_market_snapshots(connection, load_fixture()) == 2

        old_row = connection.execute(
            """
            SELECT source, market_ticker, event_ticker, side, yes_bid, yes_ask,
                   no_bid, no_ask, snapshot_timestamp, source_payload_sha256,
                   no_sub_title, title, occurrence_datetime
            FROM bronze.kalshi_market_snapshots
            WHERE market_ticker = 'OLD-TICKER-PRE-MIGRATION'
            """
        ).fetchone()
        total_count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]

    # Original pre-migration data is intact, byte-for-byte, not touched by
    # the migration.
    assert old_row[0:10] == (
        "kalshi",
        "OLD-TICKER-PRE-MIGRATION",
        "OLD-EVENT-PRE-MIGRATION",
        "OldSide",
        Decimal("0.5000"),
        Decimal("0.5500"),
        Decimal("0.4500"),
        Decimal("0.5000"),
        old_snapshot_timestamp,
        "old-payload-hash",
    )
    # The new columns exist but are NULL for the pre-migration row -- not
    # dropped, not silently defaulted to some invented value.
    assert old_row[10] is None
    assert old_row[11] is None
    assert old_row[12] is None
    # The old row plus the 2 freshly-ingested rows all coexist.
    assert total_count == 3


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
