"""Unit tests for the ML-003 XGBoost P(home_win) classifier contract."""

from __future__ import annotations

import numpy as np

from src.models.xgboost_model import MODEL_NAME, build_model, model_metadata


def _toy_dataset() -> tuple[np.ndarray, np.ndarray]:
    """Small, learnable 2D X (with NaN) and 1D binary y.

    Feature 0 separates the classes; feature 1 carries NaN to exercise the
    booster's native missing-value handling.
    """
    X = np.array(
        [
            [0.0, 1.0],
            [0.1, np.nan],
            [0.2, 0.5],
            [0.3, np.nan],
            [1.0, 0.0],
            [1.1, np.nan],
            [1.2, 0.4],
            [1.3, 1.0],
        ]
    )
    y = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    return X, y


def test_fits_and_predicts_probabilities_with_nan():
    X, y = _toy_dataset()
    model = build_model(random_state=0)
    model.fit(X, y)

    proba = model.predict_proba(X)
    assert proba.shape == (X.shape[0], 2)
    assert np.all(proba >= 0.0) and np.all(proba <= 1.0)
    # Rows sum to 1 (proper probability distribution).
    assert np.allclose(proba.sum(axis=1), 1.0)


def test_predict_proba_handles_nan_at_inference():
    X, y = _toy_dataset()
    model = build_model(random_state=0)
    model.fit(X, y)

    # An all-NaN feature-1 row must still yield a valid probability in [0, 1].
    x_new = np.array([[0.05, np.nan], [1.25, np.nan]])
    proba = model.predict_proba(x_new)
    assert proba.shape == (2, 2)
    assert np.all(np.isfinite(proba))
    assert np.all(proba >= 0.0) and np.all(proba <= 1.0)


def test_home_win_probability_column_is_column_one():
    X, y = _toy_dataset()
    model = build_model(random_state=0)
    model.fit(X, y)

    proba = model.predict_proba(X)
    p_home_win = proba[:, 1]
    # Feature 0 is high for class 1: high-feature-0 rows should carry more
    # P(home_win) than low-feature-0 rows.
    assert p_home_win[4:].mean() > p_home_win[:4].mean()


def test_deterministic_across_two_seeded_builds():
    X, y = _toy_dataset()

    a = build_model(random_state=42)
    a.fit(X, y)
    proba_a = a.predict_proba(X)

    b = build_model(random_state=42)
    b.fit(X, y)
    proba_b = b.predict_proba(X)

    assert np.array_equal(proba_a, proba_b)


def test_model_metadata_retained():
    model = build_model(random_state=7, max_depth=3)
    meta = model_metadata(model)

    assert meta["name"] == MODEL_NAME == "xgboost"
    assert meta["random_state"] == 7
    assert isinstance(meta["params"], dict)
    assert meta["params"]["random_state"] == 7
    # Override propagated into the estimator params.
    assert meta["params"]["max_depth"] == 3
