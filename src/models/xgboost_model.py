"""XGBoost probabilistic P(home_win) classifier (ML-003).

Primary nonlinear V1 candidate. Exposes the shared model contract used by the
ML-004 walk-forward evaluator (identical across the linear / tree / boosting
families):

- ``MODEL_NAME`` — short family identifier.
- ``build_model(random_state=0, **overrides)`` — returns a fitted-on-demand
  scikit-learn-compatible estimator implementing ``fit(X, y)`` /
  ``predict_proba(X)``. ``predict_proba`` returns an ``(n, 2)`` array whose
  column 1 is ``P(home_win == 1)`` in ``[0, 1]``.
- ``model_metadata(estimator)`` — ``{name, params, random_state}``.

Missing-value handling
----------------------
XGBoost learns a default split direction for missing values natively, so this
module relies on that behavior and adds **no** imputer. Feature matrices may
contain ``NaN`` and are passed straight through to the booster. This is the
distinguishing capability of the tree/boosting family versus the linear family,
which needs explicit imputation.

Determinism
-----------
``build_model`` fixes ``random_state`` and pins ``n_jobs=1`` with the ``hist``
tree method so two builds with the same seed produce identical probabilities on
identical data. Tuning infrastructure is intentionally out of scope (ML-003);
hyperparameters are fixed, reasonable defaults overridable via ``**overrides``.
"""

from __future__ import annotations

from typing import Any

from xgboost import XGBClassifier

MODEL_NAME: str = "xgboost"

# Fixed, reasonable defaults. No tuning infrastructure (ML-003 scope). n_jobs=1
# + hist keep training deterministic for a given seed.
_DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 100,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "tree_method": "hist",
    "n_jobs": 1,
    "verbosity": 0,
}


def build_model(random_state: int = 0, **overrides: Any) -> XGBClassifier:
    """Return an unfitted XGBoost classifier honoring the shared contract.

    Parameters
    ----------
    random_state:
        Seed pinned on the booster for reproducible training.
    **overrides:
        Optional ``XGBClassifier`` keyword overrides (e.g. ``max_depth``).
        These win over the fixed defaults.
    """
    params: dict[str, Any] = {**_DEFAULT_PARAMS, "random_state": random_state}
    params.update(overrides)
    return XGBClassifier(**params)


def model_metadata(estimator: XGBClassifier) -> dict[str, Any]:
    """Return ``{name, params, random_state}`` for the given estimator."""
    params = estimator.get_params()
    return {
        "name": MODEL_NAME,
        "params": params,
        "random_state": params.get("random_state"),
    }
