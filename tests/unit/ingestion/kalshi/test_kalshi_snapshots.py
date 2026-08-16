import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.kalshi import KalshiDataError, parse_kalshi_market_snapshots


FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_market_snapshots.json"


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_maps_real_market_pair_to_canonical_snapshots() -> None:
    snapshots = parse_kalshi_market_snapshots(load_fixture())

    assert len(snapshots) == 2
    first = snapshots[0]
    assert first["source"] == "kalshi"
    assert first["market_ticker"] == "KXMLBGAME-26AUG161920SEAHOU-SEA"
    assert first["event_ticker"] == "KXMLBGAME-26AUG161920SEAHOU"
    assert first["side"] == "Seattle"
    assert first["yes_bid"] == Decimal("0.4400")
    assert first["yes_ask"] == Decimal("0.4700")
    assert first["no_bid"] == Decimal("0.5300")
    assert first["no_ask"] == Decimal("0.5600")
    assert first["snapshot_timestamp"] == datetime(
        2026, 8, 13, 23, 40, 0, 213197, tzinfo=timezone.utc
    )
    assert len(first["source_payload_sha256"]) == 64
    # PIPE-006: matching inputs DATA-023's match_kalshi_market needs.
    assert first["no_sub_title"] == "Houston"
    assert first["title"] == "Seattle vs Houston Winner?"
    assert first["occurrence_datetime"] == datetime(
        2026, 8, 17, 2, 20, 0, tzinfo=timezone.utc
    )

    # Both markets come from one fetch -> same immutable payload hash.
    assert snapshots[0]["source_payload_sha256"] == snapshots[1]["source_payload_sha256"]
    assert snapshots[1]["side"] == "Houston"
    assert snapshots[1]["no_sub_title"] == "Seattle"


def test_custom_source_is_preserved() -> None:
    snapshots = parse_kalshi_market_snapshots(load_fixture(), source="kalshi-demo")
    assert all(snapshot["source"] == "kalshi-demo" for snapshot in snapshots)


def test_payload_without_markets_list_is_rejected() -> None:
    with pytest.raises(KalshiDataError, match="markets"):
        parse_kalshi_market_snapshots({"cursor": "abc"})


def test_non_object_payload_is_rejected() -> None:
    with pytest.raises(KalshiDataError, match="payload must be an object"):
        parse_kalshi_market_snapshots(["not", "a", "dict"])


def test_no_markets_for_the_day_is_a_normal_empty_result() -> None:
    assert parse_kalshi_market_snapshots({"markets": []}) == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ticker", None, "ticker is required"),
        ("ticker", "", "ticker is required"),
        ("event_ticker", None, "event_ticker is required"),
        ("yes_sub_title", None, "yes_sub_title is required"),
        ("updated_time", None, "updated_time is required"),
        ("updated_time", "not-a-time", "valid ISO-8601 timestamp"),
        ("updated_time", "2026-08-13T23:40:00", "timezone offset"),
        ("no_sub_title", None, "no_sub_title is required"),
        ("no_sub_title", "", "no_sub_title is required"),
        ("title", None, "title is required"),
        ("occurrence_datetime", None, "occurrence_datetime is required"),
        ("occurrence_datetime", "not-a-time", "valid ISO-8601 timestamp"),
        ("occurrence_datetime", "2026-08-17T02:20:00", "timezone offset"),
    ],
)
def test_missing_or_malformed_identity_fields_fail_clearly(
    field: str, value: object, message: str
) -> None:
    payload = load_fixture()
    payload["markets"][0][field] = value

    with pytest.raises(KalshiDataError, match=message):
        parse_kalshi_market_snapshots(payload)


@pytest.mark.parametrize(
    "field",
    ["yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars"],
)
def test_missing_price_field_fails_loudly(field: str) -> None:
    payload = load_fixture()
    del payload["markets"][0][field]

    with pytest.raises(KalshiDataError, match=f"{field} is required"):
        parse_kalshi_market_snapshots(payload)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "is required and must be a decimal-dollar string"),
        (0.44, "is required and must be a decimal-dollar string"),
        ("not-a-price", "must be a valid decimal price"),
        ("-0.01", "must be between 0 and 1 dollars"),
        ("1.01", "must be between 0 and 1 dollars"),
    ],
)
def test_invalid_prices_are_rejected(value: object, message: str) -> None:
    payload = load_fixture()
    payload["markets"][0]["yes_bid_dollars"] = value

    with pytest.raises(KalshiDataError, match=message):
        parse_kalshi_market_snapshots(payload)


def test_zero_price_is_valid_for_a_thin_book() -> None:
    # MLB liquidity on Kalshi is thin; an empty side of the book (no current
    # bid) is a normal "0.0000" observation, not malformed data.
    payload = load_fixture()
    payload["markets"][0]["yes_bid_dollars"] = "0.0000"

    snapshots = parse_kalshi_market_snapshots(payload)
    assert snapshots[0]["yes_bid"] == Decimal("0")


def test_markets_preserve_input_order_not_sorted_by_ticker_or_side() -> None:
    # Deterministic ordering: the parser must not silently reorder markets
    # (e.g. by sorting on ticker), which would break any downstream code
    # relying on payload order for pairing/debugging.
    payload = load_fixture()
    third = deepcopy(payload["markets"][0])
    third["ticker"] = "KXMLBGAME-26AUG161920SEAHOU-AAA-LAST"
    third["yes_sub_title"] = "ZZZ-Last"
    payload["markets"] = [payload["markets"][1], third, payload["markets"][0]]

    snapshots = parse_kalshi_market_snapshots(payload)

    assert [snapshot["side"] for snapshot in snapshots] == [
        "Houston",
        "ZZZ-Last",
        "Seattle",
    ]


def test_one_malformed_market_rejects_the_whole_batch_not_a_partial_parse() -> None:
    # Fail-fast batch semantics: a payload with N markets where one is broken
    # must not silently drop the bad one and return N-1 good snapshots. This
    # pins the current all-or-nothing behavior as an explicit regression test.
    payload = load_fixture()
    payload["markets"][1]["ticker"] = None

    with pytest.raises(KalshiDataError, match="ticker is required"):
        parse_kalshi_market_snapshots(payload)
