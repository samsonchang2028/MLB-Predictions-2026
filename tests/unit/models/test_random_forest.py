"""Unit tests for the ML-002 Random Forest model contract."""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from models import random_forest


# Small, deterministic numeric design matrix with a NaN to exercise imputation.
# Two loosely separable classes so the forest produces non-degenerate proba.
_X = [
    [0.0, 1.0],
    [0.2, 0.9],
    [np.nan, 1.1],
    [0.1, 1.2],
    [5.0, 9.0],
    [5.2, 8.8],
    [4.9, 9.1],
    [5.1, np.nan],
]
_Y = [0, 0, 0, 0, 1, 1, 1, 1]


def test_build_model_pipeline_structure() -> None:
    model = random_forest.build_model()
    assert isinstance(model, Pipeline)
    assert isinstance(model.named_steps["imputer"], SimpleImputer)
    assert isinstance(model.named_steps["classifier"], RandomForestClassifier)


def test_fit_and_predict_with_nan() -> None:
    model = random_forest.build_model()
    model.fit(_X, _Y)
    preds = model.predict(_X)
    assert len(preds) == len(_Y)
    assert set(np.unique(preds)).issubset({0, 1})


def test_predict_proba_shape_and_range() -> None:
    model = random_forest.build_model()
    model.fit(_X, _Y)
    proba = model.predict_proba(_X)

    assert proba.shape == (len(_Y), 2)
    assert np.all(proba >= 0.0)
    assert np.all(proba <= 1.0)
    # Rows are valid probability distributions.
    assert np.allclose(proba.sum(axis=1), 1.0)
    # Column 1 corresponds to P(home_win == 1).
    assert list(model.named_steps["classifier"].classes_) == [0, 1]


def test_reproducible_across_seeded_builds() -> None:
    model_a = random_forest.build_model(random_state=42)
    model_b = random_forest.build_model(random_state=42)
    model_a.fit(_X, _Y)
    model_b.fit(_X, _Y)

    proba_a = model_a.predict_proba(_X)
    proba_b = model_b.predict_proba(_X)
    assert np.array_equal(proba_a, proba_b)


def test_metadata_reports_name_params_and_seed() -> None:
    model = random_forest.build_model(random_state=7)
    meta = random_forest.model_metadata(model)

    assert meta["name"] == "random_forest"
    assert meta["random_state"] == 7
    assert isinstance(meta["params"], dict)
    # Params expose the seeded classifier for traceability.
    assert meta["params"]["classifier__random_state"] == 7
