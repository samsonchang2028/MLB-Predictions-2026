"""Leakage mutation tests for team features (ADR-002)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from features.team import build_team_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _pair(
    game_pk: int,
    day: int,
    home_id: int,
    away_id: int,
    home_score: int | None,
    away_score: int | None,
    home_won: bool | None,
) -> list[dict]:
    when = _dt(f"2024-04-{day:02d}T19:00:00")
    away_won = None if home_won is None else (not home_won)
    return [
        {
            "game_pk": game_pk,
            "team_id": home_id,
            "side": "home",
            "game_date": when,
            "season": "2024",
            "score": home_score,
            "is_winner": home_won,
        },
        {
            "game_pk": game_pk,
            "team_id": away_id,
            "side": "away",
            "game_date": when,
            "season": "2024",
            "score": away_score,
            "is_winner": away_won,
        },
    ]


def _feature_map(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["game_pk"], r["team_id"]): r for r in build_team_features(rows)}


def _baseline_schedule() -> list[dict]:
    return (
        _pair(1, 1, 10, 20, 5, 3, True)
        + _pair(2, 2, 10, 30, 2, 4, False)
        + _pair(3, 3, 40, 10, 1, 6, False)  # team 10 away win
        + _pair(4, 4, 10, 50, None, None, None)  # upcoming
    )


def test_mutating_future_results_does_not_change_earlier_features() -> None:
    baseline = _baseline_schedule()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    # Inflate the unfinished / future game 4 outcomes.
    for row in mutated:
        if row["game_pk"] == 4:
            if row["team_id"] == 10:
                row["score"] = 20
                row["is_winner"] = True
                row["league_wins"] = 999
                row["league_pct"] = "1.000"
            else:
                row["score"] = 0
                row["is_winner"] = False

    after = _feature_map(mutated)
    for key in ((1, 10), (1, 20), (2, 10), (2, 30), (3, 10), (3, 40), (4, 10), (4, 50)):
        assert after[key] == original[key], key


def test_mutating_current_game_result_does_not_change_that_games_features() -> None:
    baseline = _baseline_schedule()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    for row in mutated:
        if row["game_pk"] == 2 and row["team_id"] == 10:
            row["score"] = 99
            row["is_winner"] = True
        if row["game_pk"] == 2 and row["team_id"] == 30:
            row["score"] = 0
            row["is_winner"] = False

    after = _feature_map(mutated)

    # Game 2's own pregame features must be unchanged (current excluded).
    assert after[(2, 10)] == original[(2, 10)]
    assert after[(2, 30)] == original[(2, 30)]
    # Earlier games unchanged.
    assert after[(1, 10)] == original[(1, 10)]
    # Later games *may* change because game 2 is now a prior result for them.
    assert after[(3, 10)] != original[(3, 10)]
    assert after[(3, 10)]["wins_before"] == original[(3, 10)]["wins_before"] + 1


def test_mutating_prior_game_does_change_later_features() -> None:
    """Sanity check that the leakage test harness is sensitive when it should be."""
    baseline = _baseline_schedule()
    original = _feature_map(baseline)

    mutated = deepcopy(baseline)
    for row in mutated:
        if row["game_pk"] == 1 and row["team_id"] == 10:
            row["score"] = 0
            row["is_winner"] = False
        if row["game_pk"] == 1 and row["team_id"] == 20:
            row["score"] = 10
            row["is_winner"] = True

    after = _feature_map(mutated)
    assert after[(1, 10)] == original[(1, 10)]  # still cold-start for game 1
    assert after[(2, 10)] != original[(2, 10)]
    assert after[(2, 10)]["win_pct_before"] == 0.0
    assert after[(2, 10)]["run_diff_total_before"] == -10
