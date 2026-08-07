"""Leakage regression via the DATA-006 validation checks (ADR-002/ADR-003).

Complements ``test_team_leakage.py`` (which probes the builder directly) by
asserting the reusable validation checks certification depends on.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from validation.leakage import (
    ALL_FOLDS,
    DEV_SEASONS,
    HOLDOUT_SEASON,
    check_chronological_folds,
    check_current_game_excluded,
    check_future_mutation_invariance,
)


def _dt(day: int) -> datetime:
    return datetime(2025, 4, day, 19, tzinfo=timezone.utc)


def _pair(game_pk: int, day: int, home: int, away: int, hs: int, aws: int, home_won: bool):
    return [
        {"game_pk": game_pk, "team_id": home, "side": "home", "game_date": _dt(day),
         "season": "2025", "score": hs, "is_winner": home_won},
        {"game_pk": game_pk, "team_id": away, "side": "away", "game_date": _dt(day),
         "season": "2025", "score": aws, "is_winner": not home_won},
    ]


def _history():
    return (
        _pair(1, 1, 10, 20, 5, 3, True)
        + _pair(2, 2, 20, 10, 1, 0, True)
        + _pair(3, 4, 10, 30, 7, 2, True)
    )


def test_future_mutation_invariance_holds_on_real_shaped_history() -> None:
    result = check_future_mutation_invariance(_history())
    assert result.status == "PASS"
    assert result.blocking is False


def test_current_game_result_does_not_leak_into_its_features() -> None:
    result = check_current_game_excluded(_history())
    assert result.status == "PASS"


def test_deepcopy_of_input_is_not_required_for_determinism() -> None:
    """The check must not mutate the caller's rows."""
    rows = _history()
    snapshot = deepcopy(rows)
    check_future_mutation_invariance(rows)
    check_current_game_excluded(rows)
    assert rows == snapshot


def test_folds_never_admit_the_2026_holdout() -> None:
    assert HOLDOUT_SEASON not in DEV_SEASONS
    for train, test in ALL_FOLDS:
        assert HOLDOUT_SEASON != test
        assert HOLDOUT_SEASON not in train
    assert check_chronological_folds().status == "PASS"
