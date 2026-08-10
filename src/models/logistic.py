"""Logistic Regression P(home_win) baseline (ML-001).

Implements the shared V1 model contract (see ``models`` package docstring) so the
ML-004 walk-forward evaluator can drive every family uniformly:

- ``MODEL_NAME`` — stable family identifier.
- ``build_model(random_state=0, **overrides)`` — returns a scikit-learn
  ``Pipeline`` (SimpleImputer -> StandardScaler -> LogisticRegression) exposing
  ``fit(X, y)`` / ``predict_proba(X)``. All preprocessing lives *inside* the
  pipeline so it is fit on the training partition only; ML-004 can therefore fit
  a fresh estimator per fold with no leakage across the split boundary.
- ``model_metadata(estimator)`` — ``{name, params, random_state}``.

The estimator is deterministic: a fixed ``random_state`` and a deterministic
solver mean identical inputs produce identical probabilities.
"""

from __future__ import annotations

from typing import Any

from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_NAME: str = "logistic_regression"


def build_model(random_state: int = 0, **overrides: Any) -> Pipeline:
    """Build the logistic-regression pipeline estimator.

    Parameters
    ----------
    random_state:
        Seed threaded into ``LogisticRegression`` for deterministic fits.
    **overrides:
        Optional ``LogisticRegression`` keyword overrides (e.g. ``C``,
        ``max_iter``). Overrides are applied on top of the defaults; passing
        ``random_state`` in ``overrides`` is redundant with the positional arg.

    Returns
    -------
    sklearn.pipeline.Pipeline
        ``imputer`` (mean SimpleImputer) -> ``scaler`` (StandardScaler) ->
        ``classifier`` (LogisticRegression). ``predict_proba`` returns shape
        ``(n_samples, 2)`` with column 1 = ``P(home_win == 1)``.
    """
    classifier_params: dict[str, Any] = {
        "random_state": random_state,
        "max_iter": 1000,
    }
    classifier_params.update(overrides)

    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(**classifier_params)),
        ]
    )


def model_metadata(estimator: Pipeline) -> dict[str, Any]:
    """Return metadata describing a built/fitted estimator.

    Includes at least ``{name, params, random_state}``. ``params`` is the full
    scikit-learn parameter mapping (``get_params``); ``random_state`` is surfaced
    from the pipeline's classifier for convenience.
    """
    params = estimator.get_params(deep=True)
    classifier = estimator.named_steps["classifier"]
    return {
        "name": MODEL_NAME,
        "params": params,
        "random_state": classifier.random_state,
    }
