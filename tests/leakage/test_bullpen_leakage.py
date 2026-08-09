"""Leakage mutation tests for bullpen features (ADR-002).

Proves the bullpen builder is point-in-time safe:

- a game's own (current) bullpen line never enters that game's features,
- future bullpen appearances never rewrite earlier feature rows,
- same-day doubleheaders are ordered chronologically so the opener never sees
  the nightcap's bullpen usage (but the nightcap sees the opener's).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from features.bullpen import build_bullpen_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _team_game(
    game_pk: int,
    team_id: int,
    game_date: str,
    *,
    bullpen_outs: int,
    earned_runs: int = 1,
    hits: int = 2,
    walks: int = 1,
    pitches: int = 30,
    game_number: int = 1,
    side: str = "home",
) -> list[dict]:
    """Starter (order 1) plus a single bullpen appearance (order 2)."""
    common = {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(game_date),
        "game_type": "R",
        "game_number": game_number,
    }
    return [
        {**common, "appearance_order": 1, "is_actual_starter": True, "outs_recorded": 15},
        {
            **common,
            "appearance_order": 2,
            "is_actual_starter": False,
            "outs_recorded": bullpen_outs,
            "pitches_thrown": pitches,
            "earned_runs": earned_runs,
            "hits_allowed": hits,
            "walks": walks,
        },
    ]


def _feature_map(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["game_pk"], r["team_id"]): r for r in build_bullpen_features(rows)}


def _baseline() -> list[dict]:
    return (
        _team_game(1, 10, "2024-05-01T19:00:00", bullpen_outs=3)
        + _team_game(2, 10, "2024-05-02T19:00:00", bullpen_outs=6)
        + _team_game(3, 10, "2024-05-03T19:00:00", bullpen_outs=9)
        + _team_game(4, 10, "2024-05-04T19:00:00", bullpen_outs=12)
    )


def test_mutating_current_game_does_not_change_its_own_features() -> None:
    baseline = _baseline()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    for row in mutated:
        if row["game_pk"] == 3 and not row.get("is_actual_starter", True):
            row["outs_recorded"] = 999
            row["earned_runs"] = 999
            row["hits_allowed"] = 999
            row["walks"] = 999
            row["pitches_thrown"] = 999
    after = _feature_map(mutated)

    # Game 3's own pregame features unchanged (current excluded).
    assert after[(3, 10)] == original[(3, 10)]
    # Earlier games unchanged.
    assert after[(1, 10)] == original[(1, 10)]
    assert after[(2, 10)] == original[(2, 10)]
    # Later game 4 *does* change: game 3 is a prior result for it.
    assert after[(4, 10)] != original[(4, 10)]


def test_mutating_future_games_does_not_change_earlier_features() -> None:
    baseline = _baseline()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    for row in mutated:
        if row["game_pk"] == 4 and not row.get("is_actual_starter", True):
            row["outs_recorded"] = 999
            row["earned_runs"] = 999
    after = _feature_map(mutated)

    for key in ((1, 10), (2, 10), (3, 10)):
        assert after[key] == original[key], key


def test_appending_future_games_does_not_change_earlier_features() -> None:
    baseline = _baseline()
    original = _feature_map(baseline)

    extended = deepcopy(baseline) + _team_game(
        5, 10, "2024-05-05T19:00:00", bullpen_outs=30, earned_runs=20
    )
    after = _feature_map(extended)
    for key in ((1, 10), (2, 10), (3, 10), (4, 10)):
        assert after[key] == original[key], key


def test_mutating_prior_game_does_change_later_features() -> None:
    """Harness sensitivity: prior bullpen changes must propagate forward."""
    baseline = _baseline()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    for row in mutated:
        if row["game_pk"] == 2 and not row.get("is_actual_starter", True):
            row["outs_recorded"] = 30
            row["earned_runs"] = 10
    after = _feature_map(mutated)

    assert after[(1, 10)] == original[(1, 10)]  # before the change
    assert after[(3, 10)] != original[(3, 10)]  # game 2 is prior to game 3
    assert after[(3, 10)]["bullpen_outs_prior_1d"] == 30


def test_same_day_doubleheader_opener_excludes_nightcap_usage() -> None:
    """DH ordering: opener must not see the nightcap; nightcap sees the opener."""
    # Prior-day game so both DH games have some history to disambiguate.
    schedule = (
        _team_game(1, 10, "2024-06-01T19:00:00", bullpen_outs=3)
        # Opener (game 1 of DH): earlier first pitch + game_number 1.
        + _team_game(
            2, 10, "2024-06-02T13:00:00", bullpen_outs=6, game_number=1
        )
        # Nightcap (game 2 of DH): later first pitch + game_number 2, same date.
        + _team_game(
            3, 10, "2024-06-02T19:00:00", bullpen_outs=9, game_number=2
        )
    )
    features = _feature_map(schedule)

    opener = features[(2, 10)]
    nightcap = features[(3, 10)]

    # Opener's prior-day workload = only the 6/01 game (3 outs), NOT the nightcap.
    assert opener["bullpen_outs_prior_1d"] == 3
    assert opener["bullpen_appearances_prior_1d"] == 1

    # Nightcap's prior-day workload includes the SAME-DAY opener (6 outs) plus 6/01 (3).
    assert nightcap["bullpen_outs_prior_1d"] == 3 + 6
    assert nightcap["bullpen_appearances_prior_1d"] == 2


def test_doubleheader_mutating_nightcap_does_not_change_opener() -> None:
    """The later same-day game is 'future' relative to the opener."""
    schedule = _team_game(
        1, 10, "2024-06-02T13:00:00", bullpen_outs=6, game_number=1
    ) + _team_game(
        2, 10, "2024-06-02T19:00:00", bullpen_outs=9, game_number=2
    )
    original = _feature_map(schedule)

    mutated = deepcopy(schedule)
    for row in mutated:
        if row["game_pk"] == 2 and not row.get("is_actual_starter", True):
            row["outs_recorded"] = 999
            row["earned_runs"] = 999
    after = _feature_map(mutated)

    # Opener (game_pk 1) is chronologically earlier -> unchanged.
    assert after[(1, 10)] == original[(1, 10)]


def test_doubleheader_ordering_is_independent_of_input_order() -> None:
    schedule = _team_game(
        2, 10, "2024-06-02T19:00:00", bullpen_outs=9, game_number=2
    ) + _team_game(
        1, 10, "2024-06-02T13:00:00", bullpen_outs=6, game_number=1
    )
    features = _feature_map(schedule)
    # Regardless of input order, the game_number-1 opener excludes the nightcap.
    assert features[(1, 10)]["bullpen_outs_prior_1d"] == 0
    assert features[(2, 10)]["bullpen_outs_prior_1d"] == 6
