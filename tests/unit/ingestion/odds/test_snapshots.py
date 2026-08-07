import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.odds import OddsDataError, parse_the_odds_api_moneylines


FIXTURE = Path(__file__).parent / "fixtures" / "the_odds_api_moneylines.json"


def load_fixture() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_maps_provider_events_books_and_teams_to_canonical_snapshots() -> None:
    snapshots = parse_the_odds_api_moneylines(load_fixture(), source="odds-provider")

    assert len(snapshots) == 4
    assert snapshots[0] == {
        "source": "odds-provider",
        "source_event_id": "event-101",
        "bookmaker": "book_a",
        "outcome": "home",
        "american_price": 125,
        "snapshot_timestamp": datetime(2026, 4, 1, 18, tzinfo=timezone.utc),
        "commence_time": datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc),
    }
    assert snapshots[2]["outcome"] == "away"
    assert snapshots[2]["snapshot_timestamp"] == datetime(
        2026, 4, 1, 18, 5, tzinfo=timezone.utc
    )


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
        parse_the_odds_api_moneylines(payload)


def test_unmatched_team_outcome_is_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["name"] = "Draw"

    with pytest.raises(OddsDataError, match="does not match the event teams"):
        parse_the_odds_api_moneylines(payload)


def test_incomplete_moneyline_market_is_rejected() -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"].pop()

    with pytest.raises(OddsDataError, match="one home and one away outcome"):
        parse_the_odds_api_moneylines(payload)


@pytest.mark.parametrize("price", [None, 0, 99, -110.5, True])
def test_invalid_american_prices_are_rejected(price: object) -> None:
    payload = load_fixture()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price

    with pytest.raises(OddsDataError, match="American integer price"):
        parse_the_odds_api_moneylines(payload)
