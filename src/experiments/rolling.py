"""Rolling recent-window development experiments (ML-006, ADR-003).

Compares recent-history training windows against the expanding window by driving
the ML-004 walk-forward runner across all three model families for each rolling
history. Two histories are evaluated:

- ``rolling_2`` — the 2 seasons immediately preceding each test season,
- ``rolling_3`` — the 3 seasons immediately preceding each test season.

The folds come from :func:`evaluation.splits.rolling_folds`, which only emits
test seasons that have a full ``_ROLLING_MAX_HISTORY``-season prior history, so
every history is compared on identical test seasons and the 2026 final holdout is
never inspected.

The emitted result schema is intentionally identical to the expanding-window
experiment (ML-005) so ML-007 can concatenate the two directly:

``fold_metrics`` rows::

    {model, window, train_seasons, test_season, log_loss, brier, ece,
     roc_auc, accuracy, n_train, n_test}

``predictions`` rows::

    {model, window, test_season, game_pk, p_home_win, y_true}

where ``window`` is ``f"rolling_{h}"`` for history ``h``.
"""

from __future__ import annotations

from typing import Any, Sequence

from evaluation.runner import run_evaluation
from evaluation.splits import rolling_folds
from models import logistic, random_forest, xgboost_model

# Default V1 model families, in a stable order.
_DEFAULT_MODEL_MODULES: tuple[Any, ...] = (logistic, random_forest, xgboost_model)


def run_rolling(
    matrix: Any,
    *,
    histories: Sequence[int] = (2, 3),
    random_state: int = 0,
    model_modules: Sequence[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run rolling-window walk-forward evaluation for every family and history.

    Parameters
    ----------
    matrix:
        FEAT-004 feature matrix (dict with ``"rows"`` or a bare row sequence).
    histories:
        Rolling histories (season counts) to evaluate; defaults to ``(2, 3)``.
        Each history ``h`` uses :func:`rolling_folds` ``(h)`` and is labeled
        ``f"rolling_{h}"``.
    random_state:
        Seed threaded into every per-fold model build for determinism.
    model_modules:
        Model modules exposing ``build_model`` / ``MODEL_NAME``. Defaults to the
        three V1 families (logistic, random forest, xgboost).

    Returns
    -------
    dict
        ``{"fold_metrics": [...], "predictions": [...]}`` using the shared ML-005
        schema described in the module docstring. Rows are ordered by
        ``(history, model)`` then by the runner's fold / prediction order, so the
        output is fully deterministic. The 2026 holdout is never present because
        :func:`rolling_folds` excludes it.
    """
    modules = tuple(model_modules) if model_modules is not None else _DEFAULT_MODEL_MODULES

    fold_metrics: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []

    for history in histories:
        window = f"rolling_{history}"
        folds = rolling_folds(history)
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
                fold_metrics.append(
                    {
                        "model": model_name,
                        "window": window,
                        "train_seasons": list(fold_report["train_seasons"]),
                        "test_season": fold_report["test_season"],
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
                            "window": window,
                            "test_season": fold_report["test_season"],
                            "game_pk": entry["game_pk"],
                            "p_home_win": entry["p_home_win"],
                            "y_true": entry["y_true"],
                        }
                    )

    return {"fold_metrics": fold_metrics, "predictions": predictions}
