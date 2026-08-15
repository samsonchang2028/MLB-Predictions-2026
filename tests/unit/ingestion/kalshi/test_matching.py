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


def test_summarize_match_results_dedupes_mapped_games_across_yes_no_market_sides() -> None:
    # Kalshi issues two markets (yes/no) per real game. mapped_games must
    # count unique real games, not matched markets, or it double-counts
    # (2N markets -> N games).
    schedule = [_candidate(776001, "Houston Astros", "Seattle Mariners", EVENT_TIME)]
    yes_side = match_kalshi_market(load_fixture_market(0), schedule)
    no_side = match_kalshi_market(load_fixture_market(1), schedule)
    assert yes_side.status == MATCHED and no_side.status == MATCHED
    assert yes_side.game_pk == no_side.game_pk == 776001

    stats = summarize_match_results([yes_side, no_side])

    assert stats["matched_events"] == 2
    assert stats["mapped_games"] == 1


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


# ---------------------------------------------------------------------------
# Adversarial coverage: multi-team-city disambiguation
# ---------------------------------------------------------------------------


def test_multi_team_city_disambiguated_by_distinct_opponent_name() -> None:
    # Two same-city clubs (Cubs, White Sox) both play on the same day, but
    # against opponents whose city names are NOT themselves shared by two
    # clubs. The market only names "Chicago" + "Houston" -- containment
    # matching must use the opponent's name to pick the White Sox/Astros game
    # and must NOT let "Chicago" alone drag in the Cubs/Dodgers game.
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "Chicago"
    market["no_sub_title"] = "Houston"
    schedule = [
        _candidate(101, "Chicago Cubs", "Los Angeles Dodgers", EVENT_TIME),
        _candidate(102, "Chicago White Sox", "Houston Astros", EVENT_TIME),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.reason == "matched"
    assert result.game_pk == 102
    assert result.candidate_game_pks == (102,)


def test_crosstown_same_city_both_sides_matches_unambiguously() -> None:
    # A real Cubs-vs-White-Sox "Crosstown Classic" game: both sides are
    # "Chicago" clubs. A bare "Chicago"/"Chicago" market must still resolve
    # to the single game that actually pairs those two clubs, not raise or
    # silently drop.
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "Chicago"
    market["no_sub_title"] = "Chicago"
    schedule = [_candidate(103, "Chicago Cubs", "Chicago White Sox", EVENT_TIME)]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.game_pk == 103


def test_reciprocal_multi_city_ambiguity_with_tied_times_is_explicit_ambiguous() -> None:
    # Adversarial worst case: BOTH sides of the market are city names shared
    # by two clubs (Chicago -> Cubs/White Sox, Los Angeles -> Dodgers/
    # Angels), and two *different* real games each satisfy the loose
    # containment test -- Cubs @ Dodgers, and White Sox @ Angels. The
    # candidates span more than one distinct team pair, so this is team-
    # identity ambiguity: it must surface AMBIGUOUS with both game_pks
    # visible, never a bare pick, regardless of the (here, tied) time gap.
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "Chicago"
    market["no_sub_title"] = "Los Angeles"
    schedule = [
        _candidate(201, "Chicago Cubs", "Los Angeles Dodgers", EVENT_TIME - timedelta(minutes=30)),
        _candidate(202, "Chicago White Sox", "Los Angeles Angels", EVENT_TIME + timedelta(minutes=30)),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == AMBIGUOUS
    assert result.reason == "ambiguous_team_identity"
    assert result.game_pk is None
    assert result.candidate_game_pks == (201, 202)


def test_reciprocal_multi_city_ambiguity_near_tie_is_ambiguous_not_resolved_by_time() -> None:
    # Same reciprocal double-ambiguity setup as above, but the two candidate
    # start times are close-not-equal. Team-name evidence alone never
    # distinguished the two specific clubs, so nearest-time must NOT be
    # consulted to silently pick a winner -- this must resolve AMBIGUOUS with
    # reason "ambiguous_team_identity", the same as the tied-time case,
    # because no timestamp can safely arbitrate between two genuinely
    # different real games. (Previously pinned as MATCHED-by-nearest-time;
    # that was the Reviewer's P1 finding -- fixed here.)
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "Chicago"
    market["no_sub_title"] = "Los Angeles"
    schedule = [
        _candidate(301, "Chicago Cubs", "Los Angeles Dodgers", EVENT_TIME - timedelta(minutes=20)),
        _candidate(302, "Chicago White Sox", "Los Angeles Angels", EVENT_TIME + timedelta(minutes=25)),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == AMBIGUOUS
    assert result.reason == "ambiguous_team_identity"
    assert result.game_pk is None
    assert result.candidate_game_pks == (301, 302)


def test_reviewer_p1_regression_unrelated_same_day_collision_does_not_win_on_time() -> None:
    # Exact scenario from the Reviewer's P1 report: a Kalshi market titled
    # "Chicago" vs "New York" should match Cubs@Mets, but an UNRELATED
    # same-day White Sox@Yankees game (also a "Chicago"/"New York" collision
    # under word-subset containment) has a start time that happens to be
    # closer to occurrence_datetime. Before the fix this returned a
    # confident MATCHED on the wrong game_pk (the White Sox@Yankees game);
    # team-name evidence alone cannot distinguish which real game the market
    # refers to, so this must be AMBIGUOUS instead.
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "Chicago"
    market["no_sub_title"] = "New York"
    schedule = [
        # True target, further from occurrence_datetime in this fixture.
        _candidate(401, "Chicago Cubs", "New York Mets", EVENT_TIME - timedelta(hours=2)),
        # Unrelated same-day collision, closer in time -- must NOT win.
        _candidate(402, "Chicago White Sox", "New York Yankees", EVENT_TIME + timedelta(minutes=5)),
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == AMBIGUOUS
    assert result.reason == "ambiguous_team_identity"
    assert result.game_pk is None
    assert result.candidate_game_pks == (401, 402)


# ---------------------------------------------------------------------------
# Adversarial coverage: normalization variants (case/punctuation/whitespace)
# ---------------------------------------------------------------------------


def test_matches_with_case_punctuation_and_whitespace_variants_in_market_fields() -> None:
    market = deepcopy(load_fixture_market(0))
    market["yes_sub_title"] = "  SEATTLE.  "
    market["no_sub_title"] = "HOUSTON"
    schedule = [_candidate(401, "Houston Astros", "Seattle Mariners", EVENT_TIME)]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.game_pk == 401


def test_normalize_kalshi_team_name_handles_mixed_variants() -> None:
    variants = ["St. Louis", "st louis", "ST LOUIS", "  St.  Louis  "]
    normalized = {normalize_kalshi_team_name(v) for v in variants}
    assert normalized == {"st louis"}


def test_normalize_kalshi_team_name_period_without_following_space_collapses_words() -> None:
    # Documents a real edge case: stripping "." before whitespace-splitting
    # means an abbreviation written with NO space after the period
    # ("St.Louis") concatenates into one word instead of two, unlike
    # "St. Louis". Inherited from the same implementation pattern as
    # scripts/daily_predictions.py's _normalize_team_name (not a
    # Kalshi-specific regression) -- see tester report, P3, currently
    # unreachable since real Kalshi city names use a space after the period
    # (e.g. "St. Louis").
    assert normalize_kalshi_team_name("St.Louis") == "stlouis"
    assert normalize_kalshi_team_name("St. Louis") == "st louis"


# ---------------------------------------------------------------------------
# Adversarial coverage: 12h tolerance boundary
# ---------------------------------------------------------------------------


def test_time_tolerance_boundary_exactly_at_limit_matches() -> None:
    # Exactly 12h away is still within tolerance (strict > comparison).
    market = load_fixture_market(0)
    schedule = [_candidate(501, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=12))]

    result = match_kalshi_market(market, schedule)

    assert result.status == MATCHED
    assert result.game_pk == 501


def test_time_tolerance_boundary_one_second_past_limit_is_unmatched() -> None:
    market = load_fixture_market(0)
    schedule = [
        _candidate(
            502, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=12, seconds=1)
        )
    ]

    result = match_kalshi_market(market, schedule)

    assert result.status == UNMATCHED
    assert result.reason == "time_out_of_tolerance"
    assert result.game_pk is None
    assert result.candidate_game_pks == (502,)


# ---------------------------------------------------------------------------
# Adversarial coverage: malformed/partial market objects and empty schedule
# ---------------------------------------------------------------------------


def test_empty_schedule_is_explicit_unmatched_not_exception() -> None:
    market = load_fixture_market(0)

    result = match_kalshi_market(market, [])

    assert result.status == UNMATCHED
    assert result.reason == "no_team_match"
    assert result.game_pk is None
    assert result.candidate_game_pks == ()


@pytest.mark.parametrize("field", ["yes_sub_title", "no_sub_title", "ticker", "event_ticker"])
def test_blank_string_required_field_raises_not_silently_treated_as_missing(field: str) -> None:
    # Distinct from the "key deleted" case already covered: a present-but-
    # blank/whitespace-only string must also raise explicitly, not be
    # coerced into an empty team name that could accidentally match nothing
    # or something.
    market = deepcopy(load_fixture_market(0))
    market[field] = "   "

    with pytest.raises(KalshiMatchingError, match=field):
        match_kalshi_market(market, [])


def test_non_string_occurrence_datetime_raises() -> None:
    market = deepcopy(load_fixture_market(0))
    market["occurrence_datetime"] = None

    with pytest.raises(KalshiMatchingError, match="occurrence_datetime"):
        match_kalshi_market(market, [])


# ---------------------------------------------------------------------------
# Adversarial coverage: determinism
# ---------------------------------------------------------------------------


def test_match_result_is_deterministic_regardless_of_schedule_order() -> None:
    market = load_fixture_market(0)
    forward = [
        _candidate(601, "Houston Astros", "Seattle Mariners", EVENT_TIME - timedelta(hours=1)),
        _candidate(602, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=1)),
    ]
    backward = list(reversed(forward))

    result_forward = match_kalshi_market(market, forward)
    result_backward = match_kalshi_market(market, backward)

    assert result_forward == result_backward
    assert result_forward.status == AMBIGUOUS
    assert result_forward.candidate_game_pks == (601, 602)


def test_match_result_is_deterministic_across_repeated_calls() -> None:
    market = load_fixture_market(0)
    schedule = [_candidate(701, "Houston Astros", "Seattle Mariners", EVENT_TIME)]

    first = match_kalshi_market(market, schedule)
    second = match_kalshi_market(market, schedule)

    assert first == second


# ---------------------------------------------------------------------------
# Adversarial coverage: no-silent-drop invariant across every non-matched case
# ---------------------------------------------------------------------------


def test_every_non_matched_result_carries_an_explicit_non_empty_reason() -> None:
    market = load_fixture_market(0)
    cases = [
        [],  # no_team_match
        [_candidate(801, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(days=3))],  # time_out_of_tolerance
        [
            _candidate(802, "Houston Astros", "Seattle Mariners", EVENT_TIME - timedelta(hours=1)),
            _candidate(803, "Houston Astros", "Seattle Mariners", EVENT_TIME + timedelta(hours=1)),
        ],  # ambiguous_nearest_time
    ]

    for schedule in cases:
        result = match_kalshi_market(market, schedule)
        if result.status != MATCHED:
            assert isinstance(result.reason, str) and result.reason
            assert result.game_pk is None
