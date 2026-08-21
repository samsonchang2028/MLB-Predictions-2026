"""Leakage tests for probability calibration (ML-008, ADR-003).

Proves the inner-split calibration scheme cannot leak the evaluation partition:

- the base-fit and inner-calibration index sets are subsets of the fold's TRAIN
  indices and are DISJOINT from the test indices,
- the inner-calibration rows are chronologically AFTER the base-fit rows and
  strictly BEFORE the test season,
- mutating the test-fold feature values changes neither the fitted base model
  nor the fitted calibrator (they never observe test rows),
- the 2026 final holdout never enters any development fold's partitions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from evaluation.calibration import (
    CALIBRATION_METHODS,
    _fit_base,
    _fit_calibrator,
    _labeled,
    _partition_train,
    compare_refit_calibration,
)
from evaluation.runner import _positive_class_proba, vectorize_matrix
from evaluation.splits import (
    HOLDOUT_SEASON,
    Fold,
    development_folds,
    expanding_folds,
    fold_indices,
)
from models import logistic


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


def _matrix(per_season: int = 50, seed: int = 0, include_holdout: bool = False) -> dict:
    rng = np.random.default_rng(seed)
    seasons = (2021, 2022, 2023, 2024, 2025)
    if include_holdout:
        seasons = seasons + (2026,)
    rows = []
    game_pk = 5000
    for season in seasons:
        for _ in range(per_season):
            home = float(rng.normal())
            away = float(rng.normal())
            edge = home - away
            prob = 1.0 / (1.0 + np.exp(-edge))
            rows.append(
                {
                    "game_pk": game_pk,
                    "game_date": _dt(season, day=(game_pk % 27) + 1),
                    "features": {"home": home, "away": away, "diff": edge},
                    "target": {"home_win": bool(rng.random() < prob)},
                }
            )
            game_pk += 1
    return {"rows": rows}


def test_partitions_are_train_subsets_disjoint_from_test() -> None:
    matrix = _matrix()
    _X, _y, seasons, _cols = vectorize_matrix(matrix)
    for fold in expanding_folds():
        train_idx, test_idx = fold_indices(fold, seasons)
        base_fit_idx, cal_idx = _partition_train(train_idx, 0.2)

        train_set, test_set = set(train_idx), set(test_idx)
        # Both partitions are drawn only from the training rows.
        assert set(base_fit_idx) <= train_set
        assert set(cal_idx) <= train_set
        # Neither partition overlaps the test rows.
        assert set(base_fit_idx).isdisjoint(test_set)
        assert set(cal_idx).isdisjoint(test_set)
        # The two partitions exactly tile the training rows with no overlap.
        assert set(base_fit_idx).isdisjoint(cal_idx)
        assert set(base_fit_idx) | set(cal_idx) == train_set


def test_calibration_rows_after_base_and_before_test_season() -> None:
    matrix = _matrix()
    _X, _y, seasons, _cols = vectorize_matrix(matrix)
    for fold in expanding_folds():
        train_idx, _test_idx = fold_indices(fold, seasons)
        base_fit_idx, cal_idx = _partition_train(train_idx, 0.2)

        # Chronological: every inner-calibration row comes after every base row.
        assert max(base_fit_idx) < min(cal_idx)
        # And every calibration (and base) row is strictly before the test season.
        for i in base_fit_idx + cal_idx:
            assert seasons[i] < fold.test_season


@pytest.mark.parametrize("method", CALIBRATION_METHODS)
def test_mutating_test_rows_does_not_change_base_or_calibrator(method: str) -> None:
    matrix = _matrix()
    X, y, seasons, _cols = vectorize_matrix(matrix)
    fold = Fold((2021, 2022, 2023), 2024)
    train_idx, test_idx = fold_indices(fold, seasons)
    base_fit_idx, cal_idx = _partition_train(train_idx, 0.2)

    def fit_params(X_in: np.ndarray):
        base = _fit_base(logistic.build_model, X_in, y, base_fit_idx, random_state=0)
        X_cal, y_cal = _labeled(X_in, y, cal_idx)
        p_cal = _positive_class_proba(base, X_cal)
        calibrator = _fit_calibrator(method, p_cal, y_cal, random_state=0)
        clf = base.named_steps["classifier"]
        _kind, cal_model = calibrator
        if method == "sigmoid":
            cal_arr = np.concatenate(
                [np.ravel(cal_model.coef_), np.ravel(cal_model.intercept_)]
            )
        else:
            cal_arr = np.concatenate(
                [
                    np.ravel(cal_model.X_thresholds_),
                    np.ravel(cal_model.y_thresholds_),
                ]
            )
        base_arr = np.concatenate(
            [np.ravel(clf.coef_), np.ravel(clf.intercept_)]
        )
        return base_arr, cal_arr

    base_before, cal_before = fit_params(X)

    # Aggressively mutate ONLY the test-fold feature rows.
    X_mut = X.copy()
    for i in test_idx:
        X_mut[i, :] = X_mut[i, :] + 1_000.0

    base_after, cal_after = fit_params(X_mut)

    # The base model and calibrator never observe test rows -> identical fits.
    assert np.array_equal(base_before, base_after)
    assert np.array_equal(cal_before, cal_after)


def test_holdout_2026_never_enters_any_partition() -> None:
    matrix = _matrix(include_holdout=True)
    _X, _y, seasons, _cols = vectorize_matrix(matrix)
    holdout_positions = {i for i, s in enumerate(seasons) if s == HOLDOUT_SEASON}
    assert holdout_positions  # sanity: the fixture really contains 2026 rows

    for scheme in development_folds().values():
        for fold in scheme:
            train_idx, test_idx = fold_indices(fold, seasons)
            base_fit_idx, cal_idx = _partition_train(train_idx, 0.2)
            used = set(base_fit_idx) | set(cal_idx) | set(test_idx)
            assert holdout_positions.isdisjoint(used), fold


def test_2026_mutation_cannot_influence_refit_calibration_selection() -> None:
    matrix = _matrix(include_holdout=True)
    before = compare_refit_calibration(logistic, matrix, expanding_folds())

    mutated_rows = []
    for row in matrix["rows"]:
        copied = {**row, "features": dict(row["features"]), "target": dict(row["target"])}
        if copied["game_date"].year == HOLDOUT_SEASON:
            copied["features"] = {key: value + 10000.0 for key, value in copied["features"].items()}
            copied["target"]["home_win"] = not copied["target"]["home_win"]
        mutated_rows.append(copied)

    after = compare_refit_calibration(logistic, {"rows": mutated_rows}, expanding_folds())
    assert before == after
