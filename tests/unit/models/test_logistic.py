"""Unit tests for the Logistic Regression baseline (ML-001).

Tests build small NUMERIC arrays directly (with NaN) rather than exercising the
Gold feature-dict pivot or any fold/split logic (owned by FEAT-004 / ML-004).
"""

from __future__ import annotations

import numpy as np
import pytest

from models.logistic import MODEL_NAME, build_model, model_metadata


def _toy_dataset(n: int = 60, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Small, learnable 2-feature binary problem with some NaN entries."""
    rng = np.random.default_rng(seed)
    x0 = np.concatenate([rng.normal(-1.0, 1.0, n // 2), rng.normal(1.0, 1.0, n // 2)])
    x1 = np.concatenate([rng.normal(-1.0, 1.0, n // 2), rng.normal(1.0, 1.0, n // 2)])
    X = np.column_stack([x0, x1])
    y = np.array([0] * (n // 2) + [1] * (n // 2))
    # Inject missing values the imputer must handle.
    X[0, 0] = np.nan
    X[-1, 1] = np.nan
    return X, y


def test_fit_predict_from_array_input() -> None:
    X, y = _toy_dataset()
    model = build_model(random_state=0)
    model.fit(X, y)
    proba = model.predict_proba(X)
    assert proba.shape == (X.shape[0], 2)


def test_predict_proba_shape_and_range() -> None:
    X, y = _toy_dataset()
    model = build_model(random_state=0)
    model.fit(X, y)
    proba = model.predict_proba(X)

    assert proba.shape == (X.shape[0], 2)
    assert np.all(proba >= 0.0) and np.all(proba <= 1.0)
    # Columns are complementary; column 1 is P(home_win == 1).
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-9)


def test_determinism_identical_probabilities() -> None:
    X, y = _toy_dataset()

    a = build_model(random_state=0)
    b = build_model(random_state=0)
    a.fit(X, y)
    b.fit(X, y)

    np.testing.assert_array_equal(a.predict_proba(X), b.predict_proba(X))


def test_preprocessing_is_train_only_no_leakage() -> None:
    """Fitted preprocessing statistics must derive from TRAIN only.

    Splitting off a test partition and adding an extreme outlier to it must not
    change the fitted imputer/scaler statistics nor the transform-derived
    predictions for the unchanged train rows.
    """
    X, y = _toy_dataset(n=60, seed=1)
    X_train, y_train = X[:40], y[:40]
    X_test = X[40:].copy()

    model = build_model(random_state=0)
    model.fit(X_train, y_train)

    imputer = model.named_steps["imputer"]
    scaler = model.named_steps["scaler"]
    train_impute_stats = imputer.statistics_.copy()
    train_scaler_mean = scaler.mean_.copy()
    train_scaler_scale = scaler.scale_.copy()

    baseline_test_proba = model.predict_proba(X_test)

    # Corrupt the (separate) test array with an extreme outlier post-fit.
    X_test_outlier = X_test.copy()
    X_test_outlier[0, 0] = 1e9

    # Fitted statistics are unchanged: they came from TRAIN only.
    np.testing.assert_array_equal(imputer.statistics_, train_impute_stats)
    np.testing.assert_array_equal(scaler.mean_, train_scaler_mean)
    np.testing.assert_array_equal(scaler.scale_, train_scaler_scale)

    # Predictions for unchanged test rows are identical (transform is fixed by
    # train). The corrupted row may differ; rows 1..n must not.
    outlier_proba = model.predict_proba(X_test_outlier)
    np.testing.assert_array_equal(baseline_test_proba[1:], outlier_proba[1:])


def test_metadata_returns_name_params_seed() -> None:
    model = build_model(random_state=7)
    meta = model_metadata(model)

    assert meta["name"] == MODEL_NAME
    assert meta["random_state"] == 7
    assert isinstance(meta["params"], dict)
    # params is the sklearn parameter mapping and reflects the seed.
    assert meta["params"]["classifier__random_state"] == 7
