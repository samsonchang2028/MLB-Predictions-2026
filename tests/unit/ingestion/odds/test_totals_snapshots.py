import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.odds import OddsDataError, parse_the_odds_api_totals


FIXTURE = Path(__file__).parent / "fixtures" / "the_odds_api_totals.json"


def load_fixture() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_maps_provider_events_books_and_lines_to_canonical_snapshots() -> None:
    snapshots = parse_the_odds_api_totals(load_fixture(), source="odds-provider")

    assert len(snapshots) == 6
    assert snapshots[0] == {
        "source": "odds-provider",
        "source_event_id": "event-101",
        "bookmaker": "book_a",
        "outcome": "over",
        "point": 8.5,
        "american_price": -110,
        "snapshot_timestamp": datetime(2026, 4, 1, 18, tzinfo=timezone.utc),
        "commence_time": datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc),
        "home_team": "San Francisco Giants",
        "away_team": "Los Angeles Dodgers",
    }
    assert snapshots[1]["outcome"] == "under"
    assert snapshots[1]["point"] == 8.5
    assert snapshots[2]["point"] == 9.5
    assert snapshots[2]["american_price"] == 120
    assert snapshots[4]["bookmaker"] == "book_b"
    assert snapshots[4]["point"] == 8.0
    assert snapshots[4]["snapshot_timestamp"] == datetime(
        2026, 4, 1, 18, 5, tzinfo=timezone.utc
    )


def test_multiple_lines_per_book_are_preserved() -> None:
    snapshots = parse_the_odds_api_totals(load_fixture())
    book_a_points = sorted(
        snapshot["point"] for snapshot in snapshots if snapshot["bookmaker"] == "book_a"
    )

    assert book_a_points == [8.5, 8.5, 9.5, 9.5]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("last_update", None, "last_update is required"),
        ("last_update", "not-a-time", "valid ISO-8601 timestamp"),
        ("last_update", "2026-04-01T18:00:00", "timezone offset"),
        ("commence_time", None, "commence_time is required"),
    ],
)
def test_missing_or_malformed_timestamps_fail_clearly(
    field: str, value: object, message: str
) -> None:
    payload = load_fixture()
    if field == "commence_time":
        payload[0][field] = value
    else:
        payload[0]["bookmakers"][0][field] = value

    with pytest.raises(OddsDataError, match=message):
        parse_the_odds_api_totals(payload)


def test_missing_point_is_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0].pop("point")

    with pytest.raises(OddsDataError, match="numeric total-runs line"):
        parse_the_odds_api_totals(payload)


def test_malformed_outcome_name_is_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["name"] = "Draw"

    with pytest.raises(OddsDataError, match="must be Over or Under"):
        parse_the_odds_api_totals(payload)


def test_mismatched_over_under_points_are_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][1]["point"] = 9.0

    with pytest.raises(OddsDataError, match="share the same point"):
        parse_the_odds_api_totals(payload)


def test_incomplete_totals_market_is_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"].pop()

    with pytest.raises(OddsDataError, match="one over and one under outcome"):
        parse_the_odds_api_totals(payload)


def test_event_without_totals_market_returns_no_snapshots() -> None:
    payload = load_fixture()
    for bookmaker in payload[0]["bookmakers"]:
        for market in bookmaker["markets"]:
            market["key"] = "spreads"

    assert parse_the_odds_api_totals(payload) == []


@pytest.mark.parametrize(
    "price", [None, 0, 99, -(2**31) - 1, 2**31, -110.5, True]
)
def test_invalid_american_prices_are_rejected(price: object) -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price

    with pytest.raises(OddsDataError, match="American integer price"):
        parse_the_odds_api_totals(payload)
