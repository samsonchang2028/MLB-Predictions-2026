import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ingestion.odds import (
    PUBLISHED_SHA256,
    HistoricalOddsArchiveError,
    parse_historical_odds_archive,
)


FIXTURE = Path(__file__).parent / "fixtures" / "historical_odds_archive.json"


def load_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_source_identity_and_separate_opening_current_prices() -> None:
    records = parse_historical_odds_archive(load_fixture_bytes())

    assert records[0] == {
        "archive_date": date(2021, 4, 1),
        "source_game_index": 0,
        "source_line_index": 0,
        "game_start_time": datetime(2021, 4, 1, 17, 5, tzinfo=timezone.utc),
        "home_team": "New York Yankees",
        "home_team_abbreviation": "NYY",
        "away_team": "Toronto Blue Jays",
        "away_team_abbreviation": "TOR",
        "sportsbook": "fanduel",
        "opening_home_american": -188,
        "opening_away_american": 155,
        "current_home_american": -200,
        "current_away_american": 168,
    }
    assert records[1]["opening_away_american"] is None
    assert records[1]["current_home_american"] is None


def test_parser_emits_no_fabricated_observation_timestamp() -> None:
    records = parse_historical_odds_archive(load_fixture_bytes())

    assert records
    assert all("snapshot_timestamp" not in record for record in records)
    assert all("observation_timestamp" not in record for record in records)


def test_game_without_moneylines_does_not_fabricate_a_record() -> None:
    payload = load_fixture()
    payload["2021-04-01"].append(
        {"gameView": {}, "odds": {"moneyline": []}}
    )

    records = parse_historical_odds_archive(json.dumps(payload).encode())

    assert len(records) == 2


@pytest.mark.parametrize("price", [99, -99, 12.5, True, 2**31, -(2**31) - 1])
def test_invalid_american_odds_are_rejected(price: object) -> None:
    payload = load_fixture()
    payload["2021-04-01"][0]["odds"]["moneyline"][0]["openingLine"][
        "homeOdds"
    ] = price

    with pytest.raises(HistoricalOddsArchiveError, match="valid 32-bit American"):
        parse_historical_odds_archive(json.dumps(payload).encode())


def test_malformed_start_time_is_rejected() -> None:
    payload = load_fixture()
    payload["2021-04-01"][0]["gameView"]["startDate"] = "2021-04-01 17:05"

    with pytest.raises(HistoricalOddsArchiveError, match="timezone offset"):
        parse_historical_odds_archive(json.dumps(payload).encode())


def test_published_checksum_is_pinned() -> None:
    assert PUBLISHED_SHA256 == (
        "3f952fd0bfae9f4f2d17e66692cb936ce6e1a5f6b415318012090c85933b882b"
    )
