"""ML-010: one-time evaluation of the untouched 2026 final holdout.

This is the ONLY module authorized to let a 2026 row enter a train/test split.
:mod:`evaluation.splits` intentionally hard-refuses any 2026 presence in a
development fold -- correctly, for every ML-005/006/007/008/009 caller. This
module does not touch or relax that guard; it implements the single
2021-2025-train / 2026-test split ADR-006 authorizes, with its own explicit
checks, and reuses the runner's fit/score internals so the fit/predict path is
identical to every development fold.

Locked methodology (ADR-006): tuned shallow XGBoost, expanding window (all of
2021-2025 as training), uncalibrated probabilities. Hyperparameters below are
fixed constants, not selected or tuned here -- selection already happened on
development data before 2026 was ever read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evaluation.runner import _evaluate_fold, _vectorize
from evaluation.splits import DEV_SEASONS, HOLDOUT_SEASON
from features.completeness import require_historical_feature_completeness
from models import xgboost_model

# ADR-006 locked hyperparameters. Do not tune, select, or change these after
# 2026 has been inspected -- see
# docs/decisions/ADR-006-v1-methodology-lock.md.
LOCKED_PARAMS: dict[str, Any] = {
    "max_depth": 2,
    "learning_rate": 0.03,
    "n_estimators": 300,
    "reg_lambda": 10.0,
    "min_child_weight": 3.0,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

ADR_006_PATH = Path("docs/decisions/ADR-006-v1-methodology-lock.md")
_ADR_006_ACCEPTED_MARKER = "## Status\n\nAccepted."
_ADR_006_LOCK_MARKER = "Lock the V1 methodology as:"


def require_locked_methodology(adr_path: Path = ADR_006_PATH) -> None:
    """Refuse to run unless ADR-006's methodology lock is present and accepted.

    This is a traceability gate, not a cryptographic one: it exists so the
    2026 holdout runner cannot be invoked before a methodology decision has
    actually been recorded and accepted, and fails loudly (rather than
    silently evaluating against a stale/rejected decision) if the ADR file is
    missing or was edited out from under it.
    """
    if not adr_path.exists():
        raise RuntimeError(f"ADR-006 methodology lock not found at {adr_path}")
    text = adr_path.read_text(encoding="utf-8")
    if _ADR_006_ACCEPTED_MARKER not in text:
        raise RuntimeError(
            "ADR-006 is not in Accepted status; refusing 2026 holdout run"
        )
    if _ADR_006_LOCK_MARKER not in text:
        raise RuntimeError(
            "ADR-006 does not contain the expected methodology lock section"
        )


def _build_model(random_state: int = 0) -> Any:
    return xgboost_model.build_model(random_state=random_state, **LOCKED_PARAMS)


def run_holdout_evaluation(
    matrix: dict[str, Any],
    *,
    random_state: int = 0,
    adr_path: Path = ADR_006_PATH,
) -> dict[str, Any]:
    """Fit the ADR-006 locked model on 2021-2025 and score the 2026 holdout.

    Exactly one fit (on all of 2021-2025), exactly one scoring pass (on all of
    2026). Preprocessing lives inside the model pipeline and is fit only on
    the training partition, matching every development fold. Train seasons
    must be exactly ``DEV_SEASONS`` and the test season must be exactly
    ``HOLDOUT_SEASON``; any other season composition raises rather than
    silently narrowing or widening the holdout.
    """
    require_locked_methodology(adr_path)
    require_historical_feature_completeness(matrix)

    X, y, seasons, feature_columns, game_pks = _vectorize(matrix)

    train_idx = tuple(i for i, s in enumerate(seasons) if s in DEV_SEASONS)
    test_idx = tuple(i for i, s in enumerate(seasons) if s == HOLDOUT_SEASON)

    if not train_idx:
        raise ValueError("no 2021-2025 training rows present in matrix")
    if not test_idx:
        raise ValueError("no 2026 holdout rows present in matrix")
    if set(train_idx) & set(test_idx):
        raise ValueError("train/test row overlap in 2026 holdout split")
    if any(seasons[i] >= HOLDOUT_SEASON for i in train_idx):
        raise ValueError(
            "a training row is not strictly before the 2026 holdout season"
        )
    if any(seasons[i] != HOLDOUT_SEASON for i in test_idx):
        raise ValueError("holdout test partition contains a non-2026 row")

    metrics, y_test, p_test, game_pk_test = _evaluate_fold(
        _build_model, X, y, train_idx, test_idx, random_state, game_pks
    )
    metrics["train_seasons"] = sorted({seasons[i] for i in train_idx})
    metrics["test_season"] = HOLDOUT_SEASON

    predictions = [
        {"game_pk": pk, "p_home_win": float(p), "y_true": int(yt)}
        for pk, p, yt in zip(game_pk_test, p_test, y_test)
    ]

    return {
        "model": "xgboost_locked_adr006",
        "locked_params": LOCKED_PARAMS,
        "random_state": random_state,
        "n_features": len(feature_columns),
        "feature_columns": feature_columns,
        "metrics": metrics,
        "predictions": predictions,
    }
