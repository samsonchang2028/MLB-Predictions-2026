"""Leakage tests for the walk-forward split framework (ML-004, ADR-003).

Proves at the row level that the chronological folds cannot leak:

- train and test row-index sets are always disjoint (no row in both),
- no row from the test season or any *future* season ever enters a train fold,
- the 2026 final holdout never enters any development fold's train or test set.
"""

from __future__ import annotations

import pytest

from evaluation.splits import (
    HOLDOUT_SEASON,
    Fold,
    development_folds,
    fold_indices,
)

# One synthetic row per season index; includes the 2026 holdout so we can prove
# it is excluded from every development fold.
_ROW_SEASONS = [2021, 2022, 2023, 2024, 2025, 2026, 2021, 2025, 2023]


def test_no_train_test_row_overlap_across_all_schemes() -> None:
    for scheme in development_folds().values():
        for fold in scheme:
            train_idx, test_idx = fold_indices(fold, _ROW_SEASONS)
            assert set(train_idx).isdisjoint(test_idx), fold


def test_no_current_or_future_season_row_enters_a_train_fold() -> None:
    for scheme in development_folds().values():
        for fold in scheme:
            train_idx, _test_idx = fold_indices(fold, _ROW_SEASONS)
            for i in train_idx:
                # Strictly-before-test-season is the whole point of walk-forward.
                assert _ROW_SEASONS[i] < fold.test_season, (fold, i)


def test_holdout_2026_row_never_selected_in_any_development_fold() -> None:
    holdout_positions = {
        i for i, s in enumerate(_ROW_SEASONS) if s == HOLDOUT_SEASON
    }
    assert holdout_positions  # sanity: the fixture actually contains 2026 rows
    for scheme in development_folds().values():
        for fold in scheme:
            train_idx, test_idx = fold_indices(fold, _ROW_SEASONS)
            selected = set(train_idx) | set(test_idx)
            assert holdout_positions.isdisjoint(selected), fold


def test_fold_indices_rejects_a_holdout_referencing_fold() -> None:
    # Guard the resolver itself: a fold that references 2026 must be rejected.
    with pytest.raises(ValueError):
        fold_indices(Fold((2024, 2025), HOLDOUT_SEASON), _ROW_SEASONS)
    with pytest.raises(ValueError):
        fold_indices(Fold((HOLDOUT_SEASON,), 2025), _ROW_SEASONS)
