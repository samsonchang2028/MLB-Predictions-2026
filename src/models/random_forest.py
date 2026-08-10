"""Random Forest P(home_win) classifier (ML-002).

A minimal, reproducible baseline that conforms to the shared V1 model contract
(see ``src/models/__init__.py``) so the ML-004 walk-forward evaluator can drive
every model family uniformly:

- ``MODEL_NAME``: family identifier.
- ``build_model(...)``: returns a scikit-learn ``Pipeline`` that imputes missing
  values then fits a ``RandomForestClassifier``. Exposes ``fit(X, y)`` and
  ``predict_proba(X)``; ``predict_proba`` returns an ``(n, 2)`` array whose
  column 1 is ``P(home_win == 1)``.
- ``model_metadata(estimator)``: name, params, and the seed for traceability.

Tuning is intentionally minimal (ML-002 constraint): defaults track scikit-learn
except for a fixed ``random_state`` for reproducibility. No custom framework
abstraction is introduced for this single model.
"""

from __future__ import annotations

from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

MODEL_NAME: str = "random_forest"


def build_model(random_state: int = 0, **overrides: Any) -> Pipeline:
    """Build the Random Forest inference pipeline.

    Parameters
    ----------
    random_state:
        Seed forwarded to ``RandomForestClassifier`` for reproducible fits.
    **overrides:
        Optional keyword overrides passed straight to
        ``RandomForestClassifier`` (e.g. ``n_estimators``). Tuning is kept
        minimal per ML-002; overrides exist only so the evaluator can vary
        parameters without wrapping the estimator.

    Returns
    -------
    Pipeline
        ``SimpleImputer`` (mean strategy, tolerates NaN in ``X``) followed by a
        seeded ``RandomForestClassifier``. Implements ``fit(X, y)`` and
        ``predict_proba(X)``; ``predict_proba`` returns ``(n, 2)`` with column 1
        equal to ``P(home_win == 1)``.
    """
    classifier = RandomForestClassifier(random_state=random_state, **overrides)
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            ("classifier", classifier),
        ]
    )


def model_metadata(estimator: Pipeline) -> dict[str, Any]:
    """Return traceability metadata for a built/fitted pipeline.

    Returns a dict with ``name``, the full pipeline ``params``, and the
    classifier ``random_state`` seed.
    """
    classifier = estimator.named_steps["classifier"]
    return {
        "name": MODEL_NAME,
        "params": estimator.get_params(),
        "random_state": classifier.random_state,
    }
