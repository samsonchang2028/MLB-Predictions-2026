import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingestion.kalshi.matching import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    KalshiGameCandidate,
    KalshiMatchingError,
    kalshi_game_candidates_from_schedule,
    match_kalshi_market,
    normalize_kalshi_team_name,
    summarize_match_results,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kalshi_market_snapshots.json"
EVENT_TIME = datetime(2026, 8, 17, 2, 20, tzinfo=timezone.utc)


def load_fixture_market(index: int = 0) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["markets"][index]


def _candidate(game_pk: int, home: str, away: str, start_time: datetime) -> KalshiGameCandidate:
    return KalshiGameCandidate(
        game_pk=game_pk,
        start_time=start_time,
        home_team_norm=normalize_kalshi_team_name(home),
        away_team_norm=normalize_kalshi_team_name(away),
    )


def test_matches_real_fixture_market_to_schedule_game() -> None:
    # Real captured payload: yes_sub_title="Seattle", no_sub_title="Houston",
    # occurrence_datetime=2026-08-17T02:20:00Z.
    market = load_fixture_market(0)
    schedule = [_candidate(776001, "Houston Astros", "Seattle Mariners", EVENT_TIME)]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.reason == "matched"
    assert result.game_pk == 776001
    assert result.candidate_game_pks == (776001,)
    assert result.market_ticker == "KXMLBGAME-26AUG161920SEAHOU-SEA"
    assert result.event_ticker == "KXMLBGAME-26AUG161920SEAHOU"


def test_matches_regardless_of_which_side_the_market_is_for() -> None:
    # The Houston-side market of the same event carries the same two team
    # names (yes/no swapped) and must resolve to the same game_pk.
    market = load_fixture_market(1)
    schedule = [_candidate(776001, "Houston Astros", "Seattle Mariners", EVENT_TIME)]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.game_pk == 776001


def test_no_team_match_is_explicit_unmatched_not_none() -> None:
    market = load_fixture_market(0)
    schedule = [_candidate(1, "New York Yankees", "Boston Red Sox", EVENT_TIME)]

    result = match_kalshi_market(market, schedule)

    assert result.status == UNMATCHED
    assert result.reason == "no_team_match"
    assert result.game_pk is None
    assert result.candidate_game_pks == ()


def test_time_out_of_tolerance_is_explicit_unmatched() -> None:
    market = load_fixture_market(0)
    schedule = [
        _candidate(2, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(days=3))
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == UNMATCHED
    assert result.reason == "time_out_of_tolerance"
    assert result.game_pk is None
    assert result.candidate_game_pks == (2,)


def test_doubleheader_style_ambiguous_nearest_time_is_explicit() -> None:
    # Same two teams, two games the same day, both equidistant from the
    # Kalshi occurrence_datetime -- must not silently pick one.
    market = load_fixture_market(0)
    schedule = [
        _candidate(3, "Houston Astros", "Seattle Mariners", EVENT_TIME - timedelta(hours=1)),
        _candidate(4, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=1)),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == AMBIGUOUS
    assert result.reason == "ambiguous_nearest_time"
    assert result.game_pk is None
    assert result.candidate_game_pks == (3, 4)


def test_doubleheader_disambiguated_by_nearest_start_time() -> None:
    # Unlike the tie case above, one game here is clearly nearer -- this
    # should resolve, not go ambiguous just because two candidates share a
    # team pair.
    market = load_fixture_market(0)
    schedule = [
        _candidate(5, "Houston Astros", "Seattle Mariners", EVENT_TIME - timedelta(hours=6)),
        _candidate(6, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(minutes=1)),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.reason == "matched"
    assert result.game_pk == 6


@pytest.mark.parametrize("field", ["ticker", "event_ticker", "yes_sub_title", "no_sub_title"])
def test_missing_required_identity_field_raises(field: str) -> None:
    market = deepcopy(load_fixture_market(0))
    del market[field]

    with pytest.raises(KalshiMatchingError, match=field):
        match_kalshi_market(market, [])


def test_missing_occurrence_datetime_raises() -> None:
    market = deepcopy(load_fixture_market(0))
    del market["occurrence_datetime"]

    with pytest.raises(KalshiMatchingError, match="occurrence_datetime"):
        match_kalshi_market(market, [])


def test_malformed_occurrence_datetime_raises() -> None:
    market = deepcopy(load_fixture_market(0))
    market["occurrence_datetime"] = "not-a-timestamp"

    with pytest.raises(KalshiMatchingError, match="ISO-8601"):
        match_kalshi_market(market, [])


def test_normalize_kalshi_team_name_casefolds_and_strips_punctuation() -> None:
    assert normalize_kalshi_team_name("St. Louis") == normalize_kalshi_team_name("st louis")
    assert normalize_kalshi_team_name("  Seattle  ") == "seattle"


def test_summarize_match_results_surfaces_unmatched_reasons_like_odds_stats() -> None:
    matched = match_kalshi_market(
        load_fixture_market(0),
        [_candidate(7, "Houston Astros", "Seattle Mariners", EVENT_TIME)],
    )
    unmatched = match_kalshi_market(load_fixture_market(0), [])
    ambiguous = match_kalshi_market(
        load_fixture_market(0),
        [
            _candidate(8, "Houston Astros", "Seattle Mariners", EVENT_TIME - timedelta(hours=1)),
            _candidate(9, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=1)),
        ],
    )

    stats = summarize_match_results([matched, unmatched, ambiguous])

    assert stats["matched_events"] == 1
    assert stats["mapped_games"] == 1
    assert stats["unmatched_events.no_team_match"] == 1
    assert stats["unmatched_events.ambiguous_nearest_time"] == 1


def test_kalshi_game_candidates_from_schedule_extracts_team_names_from_source_game_json() -> None:
    games = [
        {
            "game_pk": 700001,
            "game_date": EVENT_TIME,
            "source_game_json": json.dumps(
                {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                }
            ),
        }
    ]

    candidates = kalshi_game_candidates_from_schedule(games)

    assert len(candidates) == 1
    assert candidates[0].game_pk == 700001
    assert candidates[0].home_team_norm == "houston astros"
    assert candidates[0].away_team_norm == "seattle mariners"
    assert candidates[0].start_time == EVENT_TIME


def test_kalshi_game_candidates_from_schedule_raises_on_missing_team_names() -> None:
    games = [{"game_pk": 700002, "game_date": EVENT_TIME, "source_game_json": json.dumps({})}]

    with pytest.raises(KalshiMatchingError, match="missing team names"):
        kalshi_game_candidates_from_schedule(games)
