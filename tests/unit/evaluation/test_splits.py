"""Unit tests for the deterministic walk-forward fold definitions (ML-004)."""

from __future__ import annotations

import pytest

from evaluation.splits import (
    DEV_SEASONS,
    HOLDOUT_SEASON,
    Fold,
    development_folds,
    expanding_folds,
    fold_indices,
    rolling_folds,
)


def test_expanding_folds_enumerated_exactly() -> None:
    assert expanding_folds() == [
        Fold((2021,), 2022),
        Fold((2021, 2022), 2023),
        Fold((2021, 2022, 2023), 2024),
        Fold((2021, 2022, 2023, 2024), 2025),
    ]


def test_rolling_2_season_folds_enumerated_exactly() -> None:
    assert rolling_folds(2) == [
        Fold((2022, 2023), 2024),
        Fold((2023, 2024), 2025),
    ]


def test_rolling_3_season_folds_enumerated_exactly() -> None:
    assert rolling_folds(3) == [
        Fold((2021, 2022, 2023), 2024),
        Fold((2022, 2023, 2024), 2025),
    ]


def test_fold_definitions_are_deterministic() -> None:
    assert expanding_folds() == expanding_folds()
    assert rolling_folds(2) == rolling_folds(2)
    assert rolling_folds(3) == rolling_folds(3)
    assert list(development_folds()) == ["expanding", "rolling_2", "rolling_3"]


def test_no_random_split_train_precedes_test_and_no_overlap() -> None:
    for scheme in development_folds().values():
        for fold in scheme:
            # chronological: every train season strictly precedes the test season
            assert all(s < fold.test_season for s in fold.train_seasons)
            # season-level no overlap: test season not among train seasons
            assert fold.test_season not in fold.train_seasons
            # train seasons ascending + unique (stable ordering, not random)
            assert list(fold.train_seasons) == sorted(set(fold.train_seasons))


def test_holdout_2026_never_appears_in_any_development_fold() -> None:
    assert HOLDOUT_SEASON not in DEV_SEASONS
    for scheme in development_folds().values():
        for fold in scheme:
            assert fold.test_season != HOLDOUT_SEASON
            assert HOLDOUT_SEASON not in fold.train_seasons


def test_dev_seasons_rejects_holdout_and_rolling_rejects_bad_history() -> None:
    with pytest.raises(ValueError):
        expanding_folds(seasons=(2024, 2025, HOLDOUT_SEASON))
    with pytest.raises(ValueError):
        rolling_folds(0)
    with pytest.raises(ValueError):
        rolling_folds(4)


def test_fold_indices_partitions_rows_by_season() -> None:
    # rows across 2021-2025, one index per row, in arbitrary order.
    row_seasons = [2021, 2022, 2023, 2024, 2025, 2023, 2021]
    fold = Fold((2021, 2022, 2023), 2024)
    train_idx, test_idx = fold_indices(fold, row_seasons)

    assert set(train_idx) == {0, 1, 2, 5, 6}
    assert set(test_idx) == {3}
    # No overlap between train and test row indices.
    assert set(train_idx).isdisjoint(test_idx)
    # Every training row precedes the test season.
    assert all(row_seasons[i] < fold.test_season for i in train_idx)


def test_fold_indices_rejects_future_train_row_season() -> None:
    # A fold whose train seasons include a season >= the test season is invalid
    # and must be rejected before it can leak.
    with pytest.raises(ValueError):
        fold_indices(Fold((2024, 2026), 2025), [2024, 2025, 2026])
