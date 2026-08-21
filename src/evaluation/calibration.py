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
import hashlib
import pickle
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
from features.completeness import require_historical_feature_completeness

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

    require_historical_feature_completeness(matrix)
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

    require_historical_feature_completeness(matrix)
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


def compare_refit_calibration(
    model: Any,
    matrix: Any,
    folds: Sequence[Fold],
    *,
    methods: Sequence[str] = CALIBRATION_METHODS,
    random_state: int = 0,
    calibration_fraction: float = 0.2,
    n_bins: int = 10,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Compare raw and calibrated probabilities from the same refit base model.

    Within each development fold, an earlier inner partition fits a temporary
    base model and the later inner partition fits the calibrators from that
    model's probabilities. The base model is then refit on *all* labeled fold
    training rows. Raw, sigmoid, and isotonic variants score the identical
    refit-base probabilities on the untouched test season. Thus calibration is
    the only difference between variants, while no test label fits anything.

    This is the appropriate production-candidate comparison for ML-014. The
    older :func:`compare_calibration` remains unchanged for ML-008 artifact
    compatibility.
    """
    if not folds:
        raise ValueError("at least one development fold is required")
    if not isinstance(n_bins, int) or n_bins < 2:
        raise ValueError(f"n_bins must be an integer >= 2, got {n_bins!r}")
    for method in methods:
        if method not in CALIBRATION_METHODS:
            raise ValueError(
                f"method must be one of {CALIBRATION_METHODS}, got {method!r}"
            )

    require_historical_feature_completeness(matrix)
    build = _resolve_build_model(model)
    name = model_name or _model_name(model, build)
    X, y, seasons, feature_columns = vectorize_matrix(matrix)

    variant_names = ["raw", *methods]
    fold_reports: dict[str, list[dict[str, Any]]] = {v: [] for v in variant_names}
    pooled: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {
        v: ([], []) for v in variant_names
    }

    for fold in folds:
        train_idx, test_idx = fold_indices(fold, seasons)
        base_fit_idx, cal_idx = _partition_train(train_idx, calibration_fraction)
        X_test, y_test = _labeled(X, y, test_idx)
        if len(y_test) == 0:
            raise ValueError(f"test partition has no labeled rows in fold {fold}")

        inner_base = _fit_base(build, X, y, base_fit_idx, random_state)
        X_cal, y_cal = _labeled(X, y, cal_idx)
        p_cal = _positive_class_proba(inner_base, X_cal)
        calibrators = {
            method: _fit_calibrator(method, p_cal, y_cal, random_state)
            for method in methods
        }

        refit_base = _fit_base(build, X, y, train_idx, random_state)
        p_raw = _positive_class_proba(refit_base, X_test)
        probabilities = {"raw": p_raw}
        probabilities.update(
            {
                method: _calibrate(calibrators[method], p_raw)
                for method in methods
            }
        )

        meta = {
            "n_base_fit": len(_labeled(X, y, base_fit_idx)[1]),
            "n_calibration": len(y_cal),
            "n_model_train": len(_labeled(X, y, train_idx)[1]),
            "n_test": len(y_test),
        }
        partition_trace = {
            "calibrator_base_fit": _partition_descriptor(base_fit_idx, seasons),
            "calibrator_fit": _partition_descriptor(cal_idx, seasons),
            "evaluation_base_refit": _partition_descriptor(train_idx, seasons),
            "evaluation": _partition_descriptor(test_idx, seasons),
        }
        for variant, p_test in probabilities.items():
            _require_probability_bounds(p_test, variant)
            metrics = _variant_fold_metrics(y_test, p_test, fold, meta)
            metrics["reliability_curve"] = reliability_curve(
                y_test, p_test, n_bins=n_bins
            )
            metrics["prediction_sha256"] = _prediction_sha256(p_test)
            metrics["probability_min"] = float(np.min(p_test))
            metrics["probability_max"] = float(np.max(p_test))
            metrics["partition_trace"] = partition_trace
            fold_reports[variant].append(metrics)
            pooled[variant][0].append(y_test)
            pooled[variant][1].append(p_test)

    variants_out: dict[str, Any] = {}
    for variant in variant_names:
        ys, ps = pooled[variant]
        pooled_y = np.concatenate(ys)
        pooled_p = np.concatenate(ps)
        aggregate = _probability_metrics(pooled_y, pooled_p)
        aggregate["n_folds"] = len(folds)
        aggregate["reliability_curve"] = reliability_curve(
            pooled_y, pooled_p, n_bins=n_bins
        )
        aggregate["prediction_sha256"] = _prediction_sha256(pooled_p)
        aggregate["probability_min"] = float(np.min(pooled_p))
        aggregate["probability_max"] = float(np.max(pooled_p))
        variants_out[variant] = {
            "folds": fold_reports[variant],
            "aggregate": aggregate,
        }

    stability = _fold_stability(variants_out, methods)
    recommendation = _calibration_recommendation(variants_out, stability, methods)
    return {
        "model": name,
        "random_state": random_state,
        "calibration_fraction": calibration_fraction,
        "methods": list(methods),
        "n_features": len(feature_columns),
        "temporal_methodology": {
            "development_seasons": sorted(set(seasons) - {2026}),
            "holdout_2026": "excluded from all selection partitions",
            "row_order": "game_date, game_pk",
            "calibrator_fit": (
                "later chronological calibration_fraction of each fold's "
                "training rows; probabilities produced by an earlier-only base fit"
            ),
            "base_model_evaluation_fit": "all labeled training rows in each fold",
            "evaluation": "next season only; labels never used for fitting",
        },
        "variants": variants_out,
        "fold_stability": stability,
        "recommendation": recommendation,
    }


def reliability_curve(
    y_true: np.ndarray, p: np.ndarray, *, n_bins: int = 10
) -> list[dict[str, Any]]:
    """Equal-width probability buckets with counts and observed win rates."""
    _require_probability_bounds(p, "reliability")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    buckets: list[dict[str, Any]] = []
    for bin_id in range(n_bins):
        mask = bin_ids == bin_id
        count = int(mask.sum())
        mean_probability = float(np.mean(p[mask])) if count else None
        actual_win_rate = float(np.mean(y_true[mask])) if count else None
        buckets.append(
            {
                "bin": bin_id,
                "lower": float(edges[bin_id]),
                "upper": float(edges[bin_id + 1]),
                "count": count,
                "mean_probability": mean_probability,
                "actual_win_rate": actual_win_rate,
                "calibration_gap": (
                    None
                    if count == 0
                    else float(actual_win_rate - mean_probability)
                ),
            }
        )
    return buckets


def serialize_calibrator(calibrator: tuple[str, Any]) -> bytes:
    """Serialize a fitted calibrator for trusted local artifact storage only."""
    _validate_calibrator(calibrator)
    return pickle.dumps(calibrator, protocol=pickle.HIGHEST_PROTOCOL)


def deserialize_calibrator(payload: bytes) -> tuple[str, Any]:
    """Load a trusted local calibrator artifact and validate its shape."""
    calibrator = pickle.loads(payload)  # noqa: S301 - explicitly trusted artifacts only
    _validate_calibrator(calibrator)
    return calibrator


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


def _validate_calibrator(calibrator: Any) -> None:
    if not isinstance(calibrator, tuple) or len(calibrator) != 2:
        raise ValueError("calibrator artifact must be a (method, fitted_model) tuple")
    method, model = calibrator
    if method not in CALIBRATION_METHODS:
        raise ValueError(f"unknown calibrator artifact method {method!r}")
    if method == "sigmoid" and not callable(getattr(model, "predict_proba", None)):
        raise ValueError("sigmoid calibrator artifact has no predict_proba")
    if method == "isotonic" and not callable(getattr(model, "predict", None)):
        raise ValueError("isotonic calibrator artifact has no predict")


def _require_probability_bounds(p: np.ndarray, variant: str) -> None:
    if not np.all(np.isfinite(p)) or np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError(f"{variant} produced probabilities outside [0, 1]")


def _prediction_sha256(p: np.ndarray) -> str:
    canonical = np.asarray(p, dtype="<f8")
    return hashlib.sha256(canonical.tobytes()).hexdigest()


def _partition_descriptor(
    indices: Sequence[int], seasons: Sequence[int]
) -> dict[str, Any]:
    """Identify a partition in the canonical vectorized row order."""
    if not indices:
        return {"count": 0, "row_index_start": None, "row_index_end": None, "seasons": []}
    return {
        "count": len(indices),
        "row_index_start": int(indices[0]),
        "row_index_end": int(indices[-1]),
        "seasons": sorted({int(seasons[index]) for index in indices}),
    }


def _fold_stability(
    variants: dict[str, Any], methods: Sequence[str]
) -> dict[str, Any]:
    raw_folds = variants["raw"]["folds"]
    stability: dict[str, Any] = {}
    for method in methods:
        deltas: list[dict[str, Any]] = []
        for raw, calibrated in zip(raw_folds, variants[method]["folds"]):
            deltas.append(
                {
                    "test_season": raw["test_season"],
                    "log_loss": calibrated["log_loss"] - raw["log_loss"],
                    "brier": calibrated["brier"] - raw["brier"],
                    "ece": calibrated["ece"] - raw["ece"],
                }
            )
        log_loss_deltas = np.asarray([row["log_loss"] for row in deltas])
        brier_deltas = np.asarray([row["brier"] for row in deltas])
        ece_deltas = np.asarray([row["ece"] for row in deltas])
        stability[method] = {
            "fold_count": len(deltas),
            "log_loss_improved_folds": int(np.sum(log_loss_deltas < 0.0)),
            "brier_improved_folds": int(np.sum(brier_deltas < 0.0)),
            "ece_improved_folds": int(np.sum(ece_deltas < 0.0)),
            "mean_log_loss_delta": float(np.mean(log_loss_deltas)),
            "std_log_loss_delta": float(np.std(log_loss_deltas)),
            "mean_brier_delta": float(np.mean(brier_deltas)),
            "mean_ece_delta": float(np.mean(ece_deltas)),
            "fold_deltas_calibrated_minus_raw": deltas,
        }
    return stability


def _calibration_recommendation(
    variants: dict[str, Any],
    stability: dict[str, Any],
    methods: Sequence[str],
) -> str:
    """Return a conservative pre-2026 recommendation from primary metrics.

    A calibrated method must improve pooled log loss and Brier and improve log
    loss in a strict majority of temporal folds. Otherwise raw is retained when
    no method clears those gates. This rule deliberately ignores accuracy, AUC,
    ROI, and all 2026 evidence.
    """
    raw = variants["raw"]["aggregate"]
    eligible = []
    for method in methods:
        aggregate = variants[method]["aggregate"]
        method_stability = stability[method]
        if (
            aggregate["log_loss"] < raw["log_loss"]
            and aggregate["brier"] < raw["brier"]
            and method_stability["log_loss_improved_folds"]
            > method_stability["fold_count"] / 2
        ):
            eligible.append(method)
    if not eligible:
        return "KEEP RAW"
    winner = min(
        eligible,
        key=lambda method: (
            variants[method]["aggregate"]["log_loss"],
            variants[method]["aggregate"]["brier"],
            variants[method]["aggregate"]["ece"],
        ),
    )
    return "USE PLATT" if winner == "sigmoid" else "USE ISOTONIC"


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
    if "n_model_train" in meta:
        metrics["n_model_train"] = meta["n_model_train"]
    return metrics
