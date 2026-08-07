"""Unit tests for the temporal / leakage validation checks."""

from __future__ import annotations

from datetime import datetime, timezone

from validation.leakage import (
    check_chronological_folds,
    check_current_game_excluded,
    check_future_mutation_invariance,
)


def _dt(day: int) -> datetime:
    return datetime(2024, 4, day, 19, tzinfo=timezone.utc)


def _pair(game_pk: int, day: int, home: int, away: int, hs: int | None, aws: int | None, home_won: bool | None):
    away_won = None if home_won is None else not home_won
    return [
        {"game_pk": game_pk, "team_id": home, "side": "home", "game_date": _dt(day),
         "season": "2024", "score": hs, "is_winner": home_won},
        {"game_pk": game_pk, "team_id": away, "side": "away", "game_date": _dt(day),
         "season": "2024", "score": aws, "is_winner": away_won},
    ]


def _schedule():
    return (
        _pair(1, 1, 10, 20, 5, 3, True)
        + _pair(2, 2, 10, 30, 2, 4, False)
        + _pair(3, 3, 40, 10, 1, 6, False)
    )


# --- future-mutation invariance -------------------------------------------- #
def test_future_mutation_invariance_passes_on_safe_builder() -> None:
    result = check_future_mutation_invariance(_schedule())
    assert result.status == "PASS"
    assert result.severity == "P0"


def test_future_mutation_invariance_detects_a_leaky_builder() -> None:
    """A builder that folds the whole schedule into every row must be caught."""

    def leaky_build(rows):
        total_games = len({r["game_pk"] for r in rows})
        return [
            {"game_pk": r["game_pk"], "team_id": r["team_id"], "leak": total_games}
            for r in rows
        ]

    result = check_future_mutation_invariance(_schedule(), build=leaky_build)
    assert result.status == "FAIL"
    assert result.blocking is True


def test_future_mutation_invariance_warns_on_empty() -> None:
    result = check_future_mutation_invariance([])
    assert result.status == "WARN"
    assert result.severity == "P0"


# --- current-game exclusion ------------------------------------------------ #
def test_current_game_excluded_passes_on_safe_builder() -> None:
    result = check_current_game_excluded(_schedule())
    assert result.status == "PASS"


def test_current_game_excluded_detects_own_result_leak() -> None:
    def leaky_build(rows):
        # Feature literally copies this game's own score -> leakage.
        return [
            {"game_pk": r["game_pk"], "team_id": r["team_id"], "own_score": r["score"]}
            for r in rows
        ]

    result = check_current_game_excluded(_schedule(), build=leaky_build)
    assert result.status == "FAIL"
    assert result.blocking is True


# --- chronological folds --------------------------------------------------- #
def test_chronological_folds_valid_by_default() -> None:
    assert check_chronological_folds().status == "PASS"


def test_chronological_folds_reject_holdout_in_selection() -> None:
    bad_folds = ((("2024", "2026"), "2025"),)
    result = check_chronological_folds(folds=bad_folds)
    assert result.status == "FAIL"
    assert result.blocking is True


def test_chronological_folds_reject_non_chronological_train() -> None:
    result = check_chronological_folds(folds=((("2024",), "2022"),))
    assert result.status == "FAIL"


def test_chronological_folds_reject_train_test_overlap() -> None:
    result = check_chronological_folds(folds=((("2022", "2023"), "2023"),))
    assert result.status == "FAIL"


def test_chronological_folds_reject_holdout_in_dev_seasons() -> None:
    result = check_chronological_folds(dev_seasons=("2025", "2026"))
    assert result.status == "FAIL"
