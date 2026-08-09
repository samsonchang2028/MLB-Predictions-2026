"""Leakage + regression tests for starting-pitcher features (ADR-002).

These probe the builder directly with the future-mutation invariance pattern:
build features, then mutate or append a current/future start, rebuild, and
assert every earlier row is byte-for-byte unchanged. Current and future
pitcher appearances must never influence an earlier row.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from features.starter import build_starter_features


def _dt(day: int) -> datetime:
    return datetime(2024, 4, day, 19, tzinfo=timezone.utc)


def _game(game_pk: int, day: int, *, game_type: str = "R") -> dict:
    return {
        "game_pk": game_pk,
        "game_date": _dt(day),
        "game_type": game_type,
        "season": "2024",
    }


def _start(
    game_pk: int,
    team_id: int,
    pitcher_id: int,
    *,
    side: str = "home",
    outs: int = 18,
    earned_runs: int = 2,
    hits: int = 5,
    walks: int = 1,
    strikeouts: int = 6,
    batters_faced: int = 24,
    pitches: int = 90,
) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "pitcher_id": pitcher_id,
        "appearance_order": 1,
        "is_actual_starter": True,
        "outs_recorded": outs,
        "earned_runs": earned_runs,
        "hits_allowed": hits,
        "walks": walks,
        "strikeouts": strikeouts,
        "batters_faced": batters_faced,
        "pitches_thrown": pitches,
    }


def _feature_map(appearances, games, starters=None) -> dict[tuple[int, int], dict]:
    return {
        (r["game_pk"], r["team_id"]): r
        for r in build_starter_features(appearances, games, starters)
    }


def _baseline():
    games = [_game(1, 1), _game(2, 7), _game(3, 13)]
    appearances = [
        _start(1, 10, 100, earned_runs=1),
        _start(2, 10, 100, earned_runs=3),
        _start(3, 10, 100, earned_runs=2),
    ]
    return appearances, games


def test_mutating_current_start_line_does_not_change_its_own_row() -> None:
    appearances, games = _baseline()
    original = _feature_map(appearances, games)

    mutated = deepcopy(appearances)
    for row in mutated:
        if row["game_pk"] == 2:
            row["earned_runs"] = 99
            row["hits_allowed"] = 99
            row["walks"] = 99
            row["strikeouts"] = 0
            row["outs_recorded"] = 3

    after = _feature_map(mutated, games)
    # Current game's own pregame row is unchanged (current line excluded).
    assert after[(2, 10)] == original[(2, 10)]
    # Earlier row unchanged.
    assert after[(1, 10)] == original[(1, 10)]
    # Later row *may* change: game 2 is now a prior start for game 3.
    assert after[(3, 10)] != original[(3, 10)]


def test_mutating_future_start_does_not_change_earlier_rows() -> None:
    appearances, games = _baseline()
    original = _feature_map(appearances, games)

    mutated = deepcopy(appearances)
    for row in mutated:
        if row["game_pk"] == 3:  # future relative to games 1 and 2
            row["earned_runs"] = 0
            row["strikeouts"] = 15
            row["outs_recorded"] = 27

    after = _feature_map(mutated, games)
    for key in ((1, 10), (2, 10)):
        assert after[key] == original[key], key


def test_appending_future_start_does_not_change_earlier_rows() -> None:
    appearances, games = _baseline()
    original = _feature_map(appearances, games)
    earlier_keys = [(1, 10), (2, 10), (3, 10)]

    extended_appearances = deepcopy(appearances) + [_start(4, 10, 100, earned_runs=0)]
    extended_games = games + [_game(4, 19)]
    after = _feature_map(extended_appearances, extended_games)
    for key in earlier_keys:
        assert after[key] == original[key], key


def test_mutating_prior_start_does_change_later_row() -> None:
    """Harness sensitivity: changing a prior start must move a later row."""
    appearances, games = _baseline()
    original = _feature_map(appearances, games)

    mutated = deepcopy(appearances)
    for row in mutated:
        if row["game_pk"] == 1:
            row["earned_runs"] = 20

    after = _feature_map(mutated, games)
    assert after[(1, 10)] == original[(1, 10)]  # still cold-start
    assert after[(2, 10)] != original[(2, 10)]
    assert after[(2, 10)]["season_era_before"] > original[(2, 10)]["season_era_before"]


def test_unrelated_pitcher_future_game_does_not_change_rows() -> None:
    appearances, games = _baseline()
    original = _feature_map(appearances, games)
    earlier_keys = list(original)

    extended_appearances = deepcopy(appearances) + [_start(99, 77, 555, earned_runs=0)]
    extended_games = games + [_game(99, 25)]
    after = _feature_map(extended_appearances, extended_games)
    for key in earlier_keys:
        assert after[key] == original[key], key


# ---------------------------------------------------------------------------
# Regression: changed starters and missing starters.
# ---------------------------------------------------------------------------


def test_starter_change_uses_actual_not_probable() -> None:
    """The certified actual starter drives the row, even if probable differs."""
    games = [_game(1, 1), _game(2, 7)]
    # Pitcher 100 built prior history; pitcher 200 is the listed probable but
    # pitcher 100 actually starts game 2. The actual identity must win.
    appearances = [_start(1, 10, 100, earned_runs=1), _start(2, 10, 100)]
    starters = [
        {"game_pk": 2, "team_id": 10, "side": "home",
         "actual_pitcher_id": 100, "probable_pitcher_id": 200},
    ]
    row = _feature_map(appearances, games, starters)[(2, 10)]
    assert row["starter_pitcher_id"] == 100
    assert row["starter_is_probable"] is False
    # Uses pitcher 100's prior start (game 1), not probable 200's history.
    assert row["season_starts_before"] == 1


def test_missing_boxscore_uses_actual_starter_identity() -> None:
    """No appearance line, but actual starter id is certified: use it, no line."""
    games = [_game(1, 1), _game(2, 7)]
    appearances = [_start(1, 10, 100, earned_runs=1)]  # only game 1 has a line
    starters = [
        {"game_pk": 2, "team_id": 10, "side": "home",
         "actual_pitcher_id": 100, "probable_pitcher_id": 100},
    ]
    row = _feature_map(appearances, games, starters)[(2, 10)]
    assert row["starter_pitcher_id"] == 100
    assert row["starter_known"] is True
    assert row["starter_is_probable"] is False
    # Prior game 1 line summarised; current game 2 has no line to leak.
    assert row["season_starts_before"] == 1
    assert row["season_era_before"] is not None


def test_missing_actual_falls_back_to_probable_flagged() -> None:
    games = [_game(2, 7)]
    starters = [
        {"game_pk": 2, "team_id": 10, "side": "away",
         "actual_pitcher_id": None, "probable_pitcher_id": 300},
    ]
    row = _feature_map([], games, starters)[(2, 10)]
    assert row["starter_pitcher_id"] == 300
    assert row["starter_is_probable"] is True
    assert row["starter_known"] is True
    assert row["season_starts_before"] == 0  # no prior history for 300


def test_unknown_starter_is_not_silently_attributed() -> None:
    games = [_game(2, 7)]
    starters = [
        {"game_pk": 2, "team_id": 10, "side": "home",
         "actual_pitcher_id": None, "probable_pitcher_id": None},
    ]
    row = _feature_map([], games, starters)[(2, 10)]
    assert row["starter_pitcher_id"] is None
    assert row["starter_known"] is False
    assert row["season_starts_before"] == 0
    assert row["season_era_before"] is None


def test_builder_does_not_mutate_inputs() -> None:
    appearances, games = _baseline()
    starters = [
        {"game_pk": 3, "team_id": 10, "side": "home",
         "actual_pitcher_id": 100, "probable_pitcher_id": 100},
    ]
    a_snapshot = deepcopy(appearances)
    g_snapshot = deepcopy(games)
    s_snapshot = deepcopy(starters)
    build_starter_features(appearances, games, starters)
    assert appearances == a_snapshot
    assert games == g_snapshot
    assert starters == s_snapshot
