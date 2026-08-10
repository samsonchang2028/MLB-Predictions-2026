"""Probability-calibration evaluation on walk-forward folds (ML-008, ADR-003).

Evaluates whether the selected model/window benefits from post-hoc probability
calibration (Platt/sigmoid or isotonic) *without ever leaking* the evaluation
partition into the fit of either the base model or the calibrator.

Inner-split scheme (the heart of ML-008)
----------------------------------------
For each :mod:`evaluation.splits` fold we resolve concrete row indices via
:func:`evaluation.splits.fold_indices`, which returns ``(train_idx, test_idx)``
in the runner's deterministic chronological ``(game_date, game_pk)`` order. We
then split the fold's **training rows only** into two chronological partitions:

- an **earlier base-fit partition** — the first ``1 - calibration_fraction`` of
  the training rows, used to fit the base estimator (``build_model``); and
- a **later inner-calibration partition** — the final ``calibration_fraction``
  (default 0.2) of the training rows, used to fit the calibrator on the base
  model's predicted probabilities.

The untouched TEST rows are scored last. Because both partitions are strict
subsequences of ``train_idx`` (which ``fold_indices`` already guarantees is
disjoint from ``test_idx``, chronologically before the test season, and free of
the 2026 holdout), the test fold is **never** used to fit the base model or the
calibrator, and the inner-calibration rows are chronologically *after* the
base-fit rows yet still strictly *before* the test season. 2026 is never read
(the folds exclude ``HOLDOUT_SEASON``).

Calibrator implementation
-------------------------
One simple, version-stable, deterministic approach is used (no
``CalibratedClassifierCV(cv="prefit")`` whose ``prefit`` mode churns across
scikit-learn releases):

- **sigmoid** (Platt): a :class:`~sklearn.linear_model.LogisticRegression` fit on
  the *logit* of the base model's calibration-partition probabilities, i.e.
  ``sigmoid(a * logit(p) + b)``.
- **isotonic**: a monotonic :class:`~sklearn.isotonic.IsotonicRegression`
  (``out_of_bounds="clip"``) mapping base probabilities to the calibration labels.

Metric math is **not** re-implemented: log loss / Brier / ECE (reliability) plus
secondary ROC-AUC / accuracy all come from
:func:`evaluation.runner._probability_metrics`, and vectorization / label
isolation come from :func:`evaluation.runner.vectorize_matrix`.

Degenerate partitions (an empty base-fit partition, or an inner-calibration
partition with too few labeled rows or only one class) raise a clear
``ValueError`` rather than crashing opaquely or silently mis-calibrating.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from evaluation.runner import (
    _drop_unlabeled,
    _model_name,
    _positive_class_proba,
    _probability_metrics,
    _resolve_build_model,
    vectorize_matrix,
)
from evaluation.splits import Fold, fold_indices

# Platt/sigmoid + isotonic are the ML-008 candidate calibration methods.
CALIBRATION_METHODS: tuple[str, ...] = ("sigmoid", "isotonic")

# Minimum labeled rows required in the inner-calibration partition to fit a
# calibrator; below this (or with a single class) calibration is ill-posed.
_MIN_CALIBRATION_ROWS: int = 2

# Clip probabilities before the logit so sigmoid calibration never sees +/-inf.
_EPS: float = 1e-12


def evaluate_calibration(
    model: Any,
    matrix: Any,
    folds: Sequence[Fold],
    *,
    method: str,
    random_state: int = 0,
    calibration_fraction: float = 0.2,
) -> dict[str, Any]:
    """Per-fold + aggregate metrics for one CALIBRATED pipeline.

    For every fold the base estimator is fit on the earlier base-fit partition,
    the ``method`` calibrator is fit on the base probabilities of the later
    inner-calibration partition, and the untouched test rows are scored with the
    calibrated probabilities. Aggregate metrics pool all labeled test rows.

    Returns
    -------
    dict
        ``{model, method, random_state, calibration_fraction, n_features, folds,
        aggregate}`` where each ``folds`` entry carries the runner's
        probability-quality metrics plus ``{train_seasons, test_season,
        n_base_fit, n_calibration, n_test}``.
    """
    if method not in CALIBRATION_METHODS:
        raise ValueError(
            f"method must be one of {CALIBRATION_METHODS}, got {method!r}"
        )

    build = _resolve_build_model(model)
    name = _model_name(model, build)
    X, y, seasons, feature_columns = vectorize_matrix(matrix)

    fold_reports: list[dict[str, Any]] = []
    pooled_y: list[np.ndarray] = []
    pooled_p: list[np.ndarray] = []

    for fold in folds:
        y_test, variants, meta = _fold_variants(
            build,
            X,
            y,
            seasons,
            fold,
            (method,),
            random_state,
            calibration_fraction,
            include_full_train=False,
        )
        p = variants[method]
        fold_reports.append(_variant_fold_metrics(y_test, p, fold, meta))
        pooled_y.append(y_test)
        pooled_p.append(p)

    aggregate = _probability_metrics(
        np.concatenate(pooled_y), np.concatenate(pooled_p)
    )
    aggregate["n_folds"] = len(folds)

    return {
        "model": name,
        "method": method,
        "random_state": random_state,
        "calibration_fraction": calibration_fraction,
        "n_features": len(feature_columns),
        "folds": fold_reports,
        "aggregate": aggregate,
    }


def compare_calibration(
    model: Any,
    matrix: Any,
    folds: Sequence[Fold],
    *,
    methods: Sequence[str] = CALIBRATION_METHODS,
    random_state: int = 0,
    calibration_fraction: float = 0.2,
) -> dict[str, Any]:
    """Compare UNCALIBRATED vs each calibration ``method`` on the SAME folds.

    For a fair head-to-head the ``uncalibrated`` baseline is fit on the *same*
    base-fit partition as the calibrated variants, so calibrated vs uncalibrated
    differ **only** by the calibrator. A separately labeled
    ``uncalibrated_full_train`` variant (base fit on all training rows) is also
    reported for reference.

    Returns
    -------
    dict
        ``{model, random_state, calibration_fraction, methods, n_features,
        variants}`` where ``variants`` maps each variant name
        (``"uncalibrated"``, each method, ``"uncalibrated_full_train"``) to
        ``{folds, aggregate}``. Every metrics block reports the primary
        log_loss / brier / ece (reliability) and secondary roc_auc / accuracy.
    """
    for m in methods:
        if m not in CALIBRATION_METHODS:
            raise ValueError(
                f"method must be one of {CALIBRATION_METHODS}, got {m!r}"
            )

    build = _resolve_build_model(model)
    name = _model_name(model, build)
    X, y, seasons, feature_columns = vectorize_matrix(matrix)

    variant_names = ["uncalibrated", *methods, "uncalibrated_full_train"]
    fold_reports: dict[str, list[dict[str, Any]]] = {v: [] for v in variant_names}
    pooled: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {
        v: ([], []) for v in variant_names
    }

    for fold in folds:
        y_test, variants, meta = _fold_variants(
            build,
            X,
            y,
            seasons,
            fold,
            methods,
            random_state,
            calibration_fraction,
            include_full_train=True,
        )
        for v in variant_names:
            p = variants[v]
            fold_reports[v].append(_variant_fold_metrics(y_test, p, fold, meta))
            pooled[v][0].append(y_test)
            pooled[v][1].append(p)

    variants_out: dict[str, Any] = {}
    for v in variant_names:
        ys, ps = pooled[v]
        aggregate = _probability_metrics(np.concatenate(ys), np.concatenate(ps))
        aggregate["n_folds"] = len(folds)
        variants_out[v] = {"folds": fold_reports[v], "aggregate": aggregate}

    return {
        "model": name,
        "random_state": random_state,
        "calibration_fraction": calibration_fraction,
        "methods": list(methods),
        "n_features": len(feature_columns),
        "variants": variants_out,
    }


def _fold_variants(
    build: Any,
    X: np.ndarray,
    y: np.ndarray,
    seasons: Sequence[int],
    fold: Fold,
    methods: Sequence[str],
    random_state: int,
    calibration_fraction: float,
    *,
    include_full_train: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, int]]:
    """Compute labeled test labels + per-variant test probabilities for one fold.

    The base estimator is fit on the base-fit partition only; each method's
    calibrator is fit on the base model's inner-calibration probabilities only.
    The returned ``variants`` maps ``"uncalibrated"`` and each method (and, when
    requested, ``"uncalibrated_full_train"``) to the test-row probabilities.
    """
    train_idx, test_idx = fold_indices(fold, seasons)
    base_fit_idx, cal_idx = _partition_train(train_idx, calibration_fraction)

    X_test, y_test = _labeled(X, y, test_idx)
    if len(y_test) == 0:
        raise ValueError(f"test partition has no labeled rows in fold {fold}")

    # Base model on the earlier base-fit partition ONLY (never the test rows).
    X_base, y_base = _labeled(X, y, base_fit_idx)
    base = _fit_estimator(build, X_base, y_base, random_state)
    p_base_test = _positive_class_proba(base, X_test)

    # Calibrators are fit on the base model's probabilities for the LATER inner
    # calibration partition ONLY (still strictly before the test season).
    X_cal, y_cal = _labeled(X, y, cal_idx)
    p_cal = _positive_class_proba(base, X_cal)

    variants: dict[str, np.ndarray] = {"uncalibrated": p_base_test}
    for method in methods:
        calibrator = _fit_calibrator(method, p_cal, y_cal, random_state)
        variants[method] = _calibrate(calibrator, p_base_test)

    if include_full_train:
        base_full = _fit_base(build, X, y, train_idx, random_state)
        variants["uncalibrated_full_train"] = _positive_class_proba(
            base_full, X_test
        )

    meta = {
        "n_base_fit": int(len(y_base)),
        "n_calibration": int(len(y_cal)),
        "n_test": int(len(y_test)),
    }
    return y_test, variants, meta


def _partition_train(
    train_idx: Sequence[int], calibration_fraction: float
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Split chronological train indices into (base-fit, inner-calibration).

    The final ``calibration_fraction`` of the training rows (by chronological
    order) form the inner-calibration partition; the earlier rows form the
    base-fit partition. Both are subsequences of ``train_idx`` so neither can
    overlap the test rows. Raises on a degenerate split (fraction out of range,
    empty base-fit partition, or an empty inner-calibration partition).
    """
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError(
            f"calibration_fraction must be in (0, 1), got {calibration_fraction!r}"
        )
    n = len(train_idx)
    n_cal = int(round(n * calibration_fraction))
    if n_cal < 1:
        raise ValueError(
            f"inner-calibration partition is empty (n_train={n}, "
            f"calibration_fraction={calibration_fraction})"
        )
    n_base = n - n_cal
    if n_base < 1:
        raise ValueError(
            f"base-fit partition is empty (n_train={n}, "
            f"calibration_fraction={calibration_fraction})"
        )
    base_fit_idx = tuple(train_idx[:n_base])
    cal_idx = tuple(train_idx[n_base:])
    return base_fit_idx, cal_idx


def _labeled(
    X: np.ndarray, y: np.ndarray, idx: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Select rows ``idx`` and drop undecided labels (reusing the runner's drop)."""
    rows = np.fromiter(idx, dtype=int, count=len(idx))
    if len(rows) == 0:
        return X[rows], y[rows].astype(int)
    return _drop_unlabeled(X[rows], y[rows])


def _fit_base(
    build: Any,
    X: np.ndarray,
    y: np.ndarray,
    idx: Sequence[int],
    random_state: int,
) -> Any:
    """Fit a fresh base estimator on the labeled rows of ``idx`` only."""
    X_fit, y_fit = _labeled(X, y, idx)
    return _fit_estimator(build, X_fit, y_fit, random_state)


def _fit_estimator(
    build: Any, X_fit: np.ndarray, y_fit: np.ndarray, random_state: int
) -> Any:
    """Build and fit a fresh estimator on already-labeled ``(X_fit, y_fit)``."""
    if len(y_fit) == 0:
        raise ValueError("base-fit partition has no labeled rows")
    estimator = build(random_state=random_state)
    estimator.fit(X_fit, y_fit)
    return estimator


def _fit_calibrator(
    method: str, p_cal: np.ndarray, y_cal: np.ndarray, random_state: int
) -> tuple[str, Any]:
    """Fit a calibrator on base probabilities ``p_cal`` and labels ``y_cal``.

    Returns a ``(method, fitted_model)`` pair. Raises on a degenerate inner
    calibration partition (too few rows or a single class) rather than fitting a
    meaningless calibrator.
    """
    if len(y_cal) < _MIN_CALIBRATION_ROWS:
        raise ValueError(
            f"inner-calibration partition has too few labeled rows "
            f"({len(y_cal)} < {_MIN_CALIBRATION_ROWS})"
        )
    if np.unique(y_cal).size < 2:
        raise ValueError(
            "inner-calibration partition has a single class; "
            "cannot fit a calibrator"
        )

    if method == "sigmoid":
        z = _logit(p_cal).reshape(-1, 1)
        model = LogisticRegression(random_state=random_state)
        model.fit(z, y_cal)
        return ("sigmoid", model)
    if method == "isotonic":
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(p_cal, y_cal)
        return ("isotonic", model)
    raise ValueError(f"unknown calibration method {method!r}")


def _calibrate(calibrator: tuple[str, Any], p: np.ndarray) -> np.ndarray:
    """Apply a fitted calibrator to base probabilities ``p`` -> calibrated probs."""
    kind, model = calibrator
    if kind == "sigmoid":
        z = _logit(p).reshape(-1, 1)
        return _positive_class_proba(model, z)
    # isotonic: predict returns calibrated probabilities directly (clipped).
    return np.clip(model.predict(p), 0.0, 1.0)


def _logit(p: np.ndarray) -> np.ndarray:
    """Numerically safe logit; clips into ``[_EPS, 1 - _EPS]`` first."""
    clipped = np.clip(np.asarray(p, dtype=float), _EPS, 1.0 - _EPS)
    return np.log(clipped / (1.0 - clipped))


def _variant_fold_metrics(
    y_test: np.ndarray,
    p: np.ndarray,
    fold: Fold,
    meta: dict[str, int],
) -> dict[str, Any]:
    """Runner probability-quality metrics for a fold + partition/season context."""
    metrics = _probability_metrics(y_test, p)
    metrics["train_seasons"] = list(fold.train_seasons)
    metrics["test_season"] = fold.test_season
    metrics["n_base_fit"] = meta["n_base_fit"]
    metrics["n_calibration"] = meta["n_calibration"]
    return metrics
