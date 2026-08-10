"""Expanding-window validation experiment (ML-005, ADR-003).

Drives the ML-004 walk-forward runner (:func:`evaluation.runner.run_evaluation`)
over the expanding-window folds (:func:`evaluation.splits.expanding_folds`) for
all three V1 model families and flattens the per-fold reports into a shared,
family-agnostic result schema so ML-007 can concatenate expanding / rolling
results directly.

Result schema (identical to the rolling experiment, ML-006)
-----------------------------------------------------------
``run_expanding`` returns::

    {
        "fold_metrics": [
            {
                "model": str,
                "window": "expanding",
                "train_seasons": list[int],
                "test_season": int,
                "log_loss": float,
                "brier": float,
                "ece": float,
                "roc_auc": float | None,
                "accuracy": float,
                "n_train": int,
                "n_test": int,
            },
            ...
        ],
        "predictions": [
            {
                "model": str,
                "window": "expanding",
                "test_season": int,
                "game_pk": <canonical>,
                "p_home_win": float,
                "y_true": int,
            },
            ...
        ],
    }

Primary probability-quality metrics (``log_loss`` / ``brier`` / ``ece``) and the
prediction rows come straight from ``run_evaluation(..., return_predictions=True)``;
the secondary ``roc_auc`` / ``accuracy`` are lifted from each fold report's
``"secondary"`` dict. The 2026 final holdout never appears because
``expanding_folds`` excludes it and no 2026 rows are ever passed deliberately.

Determinism: folds are fixed and stably ordered, ``model_modules`` defaults to a
fixed tuple in a fixed order, and ``random_state`` is threaded into every fit, so
two calls with the same inputs yield identical results.
"""

from __future__ import annotations

from typing import Any, Sequence

from evaluation.runner import run_evaluation
from evaluation.splits import expanding_folds
from models import logistic, random_forest, xgboost_model

_WINDOW = "expanding"

# Fixed order -> deterministic row ordering across the three families.
_DEFAULT_MODEL_MODULES: tuple[Any, ...] = (logistic, random_forest, xgboost_model)


def run_expanding(
    matrix: Any,
    *,
    random_state: int = 0,
    model_modules: Sequence[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run expanding-window validation for every model family.

    Parameters
    ----------
    matrix:
        A FEAT-004 matrix dict (with a ``"rows"`` list) or a bare sequence of
        FEAT-004 row dicts, spanning the 2021-2025 development seasons.
    random_state:
        Seed threaded into every per-fold fit for determinism.
    model_modules:
        Ordered model modules (each exposing ``build_model`` / ``MODEL_NAME``).
        Defaults to ``(logistic, random_forest, xgboost_model)``.

    Returns
    -------
    dict
        ``{"fold_metrics": [...], "predictions": [...]}`` using the shared schema
        documented in the module docstring. One ``fold_metrics`` row per
        (model, fold); one ``predictions`` row per (model, labeled test row).
    """
    modules = _DEFAULT_MODEL_MODULES if model_modules is None else tuple(model_modules)
    folds = expanding_folds()

    fold_metrics: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []

    for module in modules:
        report = run_evaluation(
            module,
            matrix,
            folds,
            random_state=random_state,
            return_predictions=True,
        )
        model_name = report["model"]

        for fold_report in report["folds"]:
            secondary = fold_report["secondary"]
            test_season = fold_report["test_season"]
            fold_metrics.append(
                {
                    "model": model_name,
                    "window": _WINDOW,
                    "train_seasons": list(fold_report["train_seasons"]),
                    "test_season": test_season,
                    "log_loss": fold_report["log_loss"],
                    "brier": fold_report["brier"],
                    "ece": fold_report["ece"],
                    "roc_auc": secondary["roc_auc"],
                    "accuracy": secondary["accuracy"],
                    "n_train": fold_report["n_train"],
                    "n_test": fold_report["n_test"],
                }
            )
            for entry in fold_report["predictions"]:
                predictions.append(
                    {
                        "model": model_name,
                        "window": _WINDOW,
                        "test_season": test_season,
                        "game_pk": entry["game_pk"],
                        "p_home_win": entry["p_home_win"],
                        "y_true": entry["y_true"],
                    }
                )

    return {"fold_metrics": fold_metrics, "predictions": predictions}
