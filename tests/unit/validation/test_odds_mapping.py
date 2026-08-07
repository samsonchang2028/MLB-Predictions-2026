"""Unit tests for DATA-009 odds-archive mapping decisions and normalization."""

from __future__ import annotations

from datetime import date, datetime, timezone

from validation.odds_mapping import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    ArchiveEvent,
    GameCandidate,
    build_archive_events,
    build_coverage_report,
    decide_mapping,
    map_archive_events,
    normalize_team_name,
)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _line(
    archive_date: date,
    game_index: int,
    line_index: int,
    *,
    home: str,
    away: str,
    start: datetime,
    sportsbook: str,
    opening_home: int | None = -120,
    opening_away: int | None = 110,
    current_home: int | None = -115,
    current_away: int | None = 105,
) -> dict:
    return {
        "archive_date": archive_date,
        "source_game_index": game_index,
        "source_line_index": line_index,
        "game_start_time": start,
        "home_team": home,
        "away_team": away,
        "sportsbook": sportsbook,
        "opening_home_american": opening_home,
        "opening_away_american": opening_away,
        "current_home_american": current_home,
        "current_away_american": current_away,
    }


def _candidate(
    game_pk: int,
    day: date,
    start: datetime | None,
    home: str,
    away: str,
) -> GameCandidate:
    return GameCandidate(
        game_pk=game_pk,
        official_date=day,
        start_time=start,
        home_team_norm=normalize_team_name(home),
        away_team_norm=normalize_team_name(away),
    )


# --------------------------------------------------------------------------- #
# Team-name normalization
# --------------------------------------------------------------------------- #
def test_normalization_is_deterministic_and_idempotent() -> None:
    variants = [
        "New York Yankees",
        "  new york   yankees ",
        "NEW YORK YANKEES",
    ]
    keys = {normalize_team_name(v) for v in variants}
    assert keys == {"new york yankees"}
    once = normalize_team_name("St. Louis Cardinals")
    assert once == "st louis cardinals"
    assert normalize_team_name(once) == once  # idempotent


def test_normalization_strips_diacritics_and_periods() -> None:
    assert normalize_team_name("Montréal Expos") == "montreal expos"
    assert normalize_team_name("St.Louis") == "stlouis"


# --------------------------------------------------------------------------- #
# Event aggregation
# --------------------------------------------------------------------------- #
def test_build_events_groups_sportsbook_lines_into_one_event() -> None:
    day = date(2023, 5, 1)
    start = _utc(2023, 5, 1, 23, 10)
    records = [
        _line(day, 0, 1, home="Yankees", away="Red Sox", start=start, sportsbook="B"),
        _line(day, 0, 0, home="Yankees", away="Red Sox", start=start, sportsbook="A"),
    ]
    events = build_archive_events(records)
    assert len(events) == 1
    assert events[0].sportsbooks == ("A", "B")  # sorted, deduplicated view
    assert events[0].home_team_norm == "yankees"
    assert len(events[0].lines) == 2


def test_build_events_rejects_conflicting_lines() -> None:
    day = date(2023, 5, 1)
    records = [
        _line(day, 0, 0, home="Yankees", away="Red Sox",
              start=_utc(2023, 5, 1, 23, 10), sportsbook="A"),
        _line(day, 0, 1, home="Yankees", away="Mets",
              start=_utc(2023, 5, 1, 23, 10), sportsbook="B"),
    ]
    try:
        build_archive_events(records)
    except ValueError as error:
        assert "conflicting away_team" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected conflicting-line ValueError")


# --------------------------------------------------------------------------- #
# Mapping decisions
# --------------------------------------------------------------------------- #
def _event(day: date, home: str, away: str, start: datetime, index: int = 0) -> ArchiveEvent:
    (event,) = build_archive_events(
        [_line(day, index, 0, home=home, away=away, start=start, sportsbook="A")]
    )
    return event


def test_unique_date_team_candidate_matches() -> None:
    day = date(2023, 5, 1)
    start = _utc(2023, 5, 1, 23, 10)
    event = _event(day, "Yankees", "Red Sox", start)
    candidates = [_candidate(1, day, start, "Yankees", "Red Sox")]
    mapping = decide_mapping(event, candidates)
    assert mapping.status == MATCHED
    assert mapping.game_pk == 1
    assert mapping.reason == "unique_date_team_candidate"
    assert mapping.candidate_count == 1
    assert mapping.resolved_by == "date_team_unique"


def test_no_candidate_is_unmatched() -> None:
    day = date(2023, 5, 1)
    event = _event(day, "Yankees", "Red Sox", _utc(2023, 5, 1, 23, 10))
    mapping = decide_mapping(event, [_candidate(1, day, None, "Cubs", "Reds")])
    assert mapping.status == UNMATCHED
    assert mapping.game_pk is None
    assert mapping.candidate_count == 0


def test_doubleheader_resolved_by_exact_start_time() -> None:
    day = date(2023, 7, 4)
    game1_start = _utc(2023, 7, 4, 17, 5)
    game2_start = _utc(2023, 7, 4, 23, 40)
    event = _event(day, "Yankees", "Red Sox", game2_start)
    candidates = [
        _candidate(101, day, game1_start, "Yankees", "Red Sox"),
        _candidate(102, day, game2_start, "Yankees", "Red Sox"),
    ]
    mapping = decide_mapping(event, candidates)
    assert mapping.status == MATCHED
    assert mapping.game_pk == 102
    assert mapping.reason == "unique_start_time_match_among_candidates"
    assert mapping.candidate_count == 2  # full ambiguity pool preserved for audit
    assert mapping.candidate_game_pks == (101, 102)
    assert mapping.resolved_by == "start_time_unique"


def test_doubleheader_without_start_time_match_is_ambiguous() -> None:
    day = date(2023, 7, 4)
    # Archive start time matches neither scheduled game -> never guess.
    event = _event(day, "Yankees", "Red Sox", _utc(2023, 7, 4, 20, 0))
    candidates = [
        _candidate(101, day, _utc(2023, 7, 4, 17, 5), "Yankees", "Red Sox"),
        _candidate(102, day, _utc(2023, 7, 4, 23, 40), "Yankees", "Red Sox"),
    ]
    mapping = decide_mapping(event, candidates)
    assert mapping.status == AMBIGUOUS
    assert mapping.game_pk is None
    assert mapping.reason == "doubleheader_no_start_time_match"
    assert mapping.candidate_game_pks == (101, 102)


def test_same_day_same_team_identical_start_is_ambiguous() -> None:
    day = date(2023, 7, 4)
    start = _utc(2023, 7, 4, 17, 5)
    event = _event(day, "Yankees", "Red Sox", start)
    candidates = [
        _candidate(101, day, start, "Yankees", "Red Sox"),
        _candidate(102, day, start, "Yankees", "Red Sox"),
    ]
    mapping = decide_mapping(event, candidates)
    assert mapping.status == AMBIGUOUS
    assert mapping.reason == "doubleheader_multiple_start_time_matches"
    assert mapping.game_pk is None


def test_swapped_home_away_does_not_attach() -> None:
    day = date(2023, 5, 1)
    start = _utc(2023, 5, 1, 23, 10)
    # Archive event has orientation reversed vs the scheduled game.
    event = _event(day, "Red Sox", "Yankees", start)
    candidates = [_candidate(1, day, start, "Yankees", "Red Sox")]
    mapping = decide_mapping(event, candidates)
    assert mapping.status == UNMATCHED
    assert mapping.game_pk is None


def test_mapping_is_deterministic_regardless_of_input_order() -> None:
    day = date(2023, 5, 1)
    start = _utc(2023, 5, 1, 23, 10)
    events = [
        _event(day, "Cubs", "Reds", start, index=2),
        _event(day, "Yankees", "Red Sox", start, index=0),
    ]
    candidates = [
        _candidate(1, day, start, "Yankees", "Red Sox"),
        _candidate(2, day, start, "Cubs", "Reds"),
    ]
    first = map_archive_events(events, candidates)
    second = map_archive_events(list(reversed(events)), list(reversed(candidates)))
    assert first == second
    assert [m.event_id for m in first] == [(day, 0), (day, 2)]


# --------------------------------------------------------------------------- #
# Coverage report
# --------------------------------------------------------------------------- #
def test_coverage_report_by_season_date_sportsbook() -> None:
    day = date(2022, 4, 8)
    start = _utc(2022, 4, 8, 23, 10)
    records = [
        _line(day, 0, 0, home="Yankees", away="Red Sox", start=start, sportsbook="DraftKings"),
        _line(day, 0, 1, home="Yankees", away="Red Sox", start=start, sportsbook="FanDuel",
              opening_home=None, opening_away=None),
        _line(day, 1, 0, home="Cubs", away="Reds", start=_utc(2022, 4, 8, 20, 20),
              sportsbook="DraftKings"),
    ]
    events = build_archive_events(records)
    candidates = [_candidate(1, day, start, "Yankees", "Red Sox")]  # only event 0 maps
    mappings = map_archive_events(events, candidates)
    report = build_coverage_report(events, mappings)

    assert report["totals"]["events"] == 2
    assert report["totals"][MATCHED] == 1
    assert report["totals"][UNMATCHED] == 1
    assert report["totals"]["matched_with_opening_pair"] == 1
    assert report["by_season"]["2022"]["events"] == 2
    assert report["by_date"]["2022-04-08"][MATCHED] == 1
    # DraftKings appears on both events; FanDuel only on the matched one.
    assert report["by_sportsbook"]["DraftKings"]["lines"] == 2
    assert report["by_sportsbook"]["FanDuel"]["lines"] == 1
    assert report["by_sportsbook"]["FanDuel"]["opening_pair_present"] == 0
    # Deterministic: JSON-friendly sorted keys.
    assert list(report["by_sportsbook"]) == ["DraftKings", "FanDuel"]
