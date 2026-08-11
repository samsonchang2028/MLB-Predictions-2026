"""Walk-forward evaluation runner shared by all V1 model families (ML-004).

This module owns the FEAT-004 feature-dict -> numeric matrix vectorization and
drives every model family (logistic / random forest / xgboost) uniformly over the
:mod:`evaluation.splits` folds.

Gold completeness gate (FEAT-006)
    A published FEAT-004 matrix must carry a strict historical completeness PASS
    before any estimator is built. Inference-mode matrices are refused. Bare row
    sequences remain supported only for focused evaluator tests.

Vectorization
    ``vectorize_matrix`` builds ``X`` from the **sorted union** of feature keys
    observed across all rows (missing values -> ``NaN``). Because the column set
    is derived from the whole matrix and sorted, the schema is identical across
    folds and builds; this also guards the known FEAT-004 diff-column presence
    caveat (a build that happens to omit a ``diff_*`` column still yields the same
    ordered schema, filled with ``NaN``). ``y`` is read only from the isolated
    ``row["target"]["home_win"]`` slot -- never from features. Rows are ordered
    chronologically (``game_date`` then ``game_pk``) for deterministic indices.

Per-fold fit (no leakage)
    For each fold a **fresh** estimator is built via the model's
    ``build_model(random_state=...)`` and fit on the training rows only. All
    preprocessing lives inside the model pipeline, so per-fold fitting satisfies
    "preprocessing fit inside each fold" -- no transformer is ever fit on the full
    dataset. Rows with an undecided (``None``) label are dropped from train/test.

Metrics
    Primary probability-quality metrics (ADR-003) are reported per fold and pooled
    across all test rows: log loss, Brier score, and expected calibration error
    (ECE). ROC-AUC and accuracy are reported but are explicitly secondary.
    Degenerate single-class test folds are handled: ROC-AUC is ``None`` (undefined)
    while log loss / Brier / ECE remain well-defined via fixed ``labels``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from evaluation.splits import Fold, fold_indices
from features.completeness import require_historical_feature_completeness

# Model families expose ``build_model(random_state, **overrides) -> estimator``.
BuildModel = Callable[..., Any]


def run_evaluation(
    model: Any,
    matrix: Any,
    folds: Sequence[Fold],
    *,
    random_state: int = 0,
    model_name: str | None = None,
    return_predictions: bool = False,
) -> dict[str, Any]:
    """Evaluate one model family over ``folds`` of ``matrix``.

    Parameters
    ----------
    model:
        Either a ``build_model`` callable or a model module exposing
        ``build_model`` (and, ideally, ``MODEL_NAME``). The identical call works
        for logistic, random_forest, and xgboost.
    matrix:
        The FEAT-004 matrix dict (with a ``"rows"`` list) or a bare sequence of
        FEAT-004 row dicts.
    folds:
        Ordered folds from :mod:`evaluation.splits`.
    random_state:
        Seed threaded into every per-fold ``build_model`` call for determinism.
    model_name:
        Optional override for the reported family name.
    return_predictions:
        When ``False`` (default) the return shape/behavior is unchanged. When
        ``True`` each per-fold report gains a ``"predictions"`` list with one
        ``{game_pk, p_home_win, y_true}`` entry per **labeled** test row of that
        fold, aligned to the exact probabilities/labels the fold's metrics use
        (i.e. after the identical unlabeled-drop) and ordered by the runner's
        chronological ``(game_date, game_pk)`` row order.

    Returns
    -------
    dict
        ``{model, random_state, n_features, feature_columns, folds, aggregate}``
        where ``folds`` is a per-fold metrics list and ``aggregate`` pools all
        test-fold predictions.
    """
    require_historical_feature_completeness(matrix)
    build = _resolve_build_model(model)
    name = model_name or _model_name(model, build)

    X, y, seasons, feature_columns, game_pks = _vectorize(matrix)

    fold_reports: list[dict[str, Any]] = []
    pooled_y: list[np.ndarray] = []
    pooled_p: list[np.ndarray] = []

    for fold in folds:
        train_idx, test_idx = fold_indices(fold, seasons)
        metrics, y_test, p_test, game_pk_test = _evaluate_fold(
            build, X, y, train_idx, test_idx, random_state, game_pks
        )
        metrics["train_seasons"] = list(fold.train_seasons)
        metrics["test_season"] = fold.test_season
        if return_predictions:
            metrics["predictions"] = [
                {
                    "game_pk": pk,
                    "p_home_win": float(p),
                    "y_true": int(yt),
                }
                for pk, p, yt in zip(game_pk_test, p_test, y_test)
            ]
        fold_reports.append(metrics)
        pooled_y.append(y_test)
        pooled_p.append(p_test)

    aggregate = _probability_metrics(
        np.concatenate(pooled_y), np.concatenate(pooled_p)
    )
    aggregate["n_folds"] = len(folds)

    return {
        "model": name,
        "random_state": random_state,
        "n_features": len(feature_columns),
        "feature_columns": feature_columns,
        "folds": fold_reports,
        "aggregate": aggregate,
    }


def vectorize_matrix(
    matrix: Any,
) -> tuple[np.ndarray, np.ndarray, list[int], list[str]]:
    """Convert FEAT-004 rows to ``(X, y, row_seasons, feature_columns)``.

    ``feature_columns`` is the sorted union of feature keys across all rows;
    ``X`` fills missing/non-numeric entries with ``NaN``. ``y`` holds the isolated
    ``home_win`` label as ``{0.0, 1.0}`` (``NaN`` for undecided). Rows are ordered
    chronologically by ``(game_date, game_pk)`` so indices are deterministic.
    """
    X, y, seasons, feature_columns, _game_pks = _vectorize(matrix)
    return X, y, seasons, feature_columns


def _vectorize(
    matrix: Any,
) -> tuple[np.ndarray, np.ndarray, list[int], list[str], list[Any]]:
    """Internal vectorizer that also returns the per-row canonical ``game_pk``.

    Identical to :func:`vectorize_matrix` but additionally returns ``game_pks``
    in the same chronological ``(game_date, game_pk)`` row order, so callers can
    recover per-row identity without re-implementing the sort. The public
    :func:`vectorize_matrix` 4-tuple is preserved by dropping ``game_pks``.
    """
    rows = _matrix_rows(matrix)
    rows = sorted(rows, key=lambda r: (r["game_date"], r["game_pk"]))

    feature_columns = _feature_columns(rows)
    col_index = {col: j for j, col in enumerate(feature_columns)}

    X = np.full((len(rows), len(feature_columns)), np.nan, dtype=float)
    y = np.full(len(rows), np.nan, dtype=float)
    seasons: list[int] = []
    game_pks: list[Any] = []

    for i, row in enumerate(rows):
        features = row.get("features", {}) or {}
        for col, value in features.items():
            numeric = _as_float(value)
            if numeric is not None:
                X[i, col_index[col]] = numeric
        label = _as_label(row.get("target", {}).get("home_win"))
        if label is not None:
            y[i] = label
        seasons.append(_row_season(row))
        game_pks.append(row["game_pk"])

    return X, y, seasons, feature_columns, game_pks


def _evaluate_fold(
    build: BuildModel,
    X: np.ndarray,
    y: np.ndarray,
    train_idx: tuple[int, ...],
    test_idx: tuple[int, ...],
    random_state: int,
    game_pks: Sequence[Any] | None = None,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray, list[Any]]:
    """Fit a fresh estimator on the train rows and score the test rows.

    Returns ``(metrics, y_test, p_test, game_pk_test)`` where ``game_pk_test`` is
    the canonical ``game_pk`` for each **labeled** test row, in the same order as
    ``y_test``/``p_test`` and filtered by the identical unlabeled-drop mask, so
    the game_pk/probability/label triples stay aligned.
    """
    train = np.fromiter(train_idx, dtype=int, count=len(train_idx))
    test = np.fromiter(test_idx, dtype=int, count=len(test_idx))

    X_train, y_train = _drop_unlabeled(X[train], y[train])
    # Identical unlabeled-drop as _drop_unlabeled, but the mask is retained so the
    # test-row game_pks can be filtered in lockstep with X_test/y_test.
    test_mask = ~np.isnan(y[test])
    X_test = X[test][test_mask]
    y_test = y[test][test_mask].astype(int)
    if len(y_train) == 0:
        raise ValueError("training partition has no labeled rows")
    if len(y_test) == 0:
        raise ValueError("test partition has no labeled rows")

    estimator = build(random_state=random_state)
    estimator.fit(X_train, y_train)
    p_test = _positive_class_proba(estimator, X_test)

    metrics = _probability_metrics(y_test, p_test)
    metrics["n_train"] = int(len(y_train))

    if game_pks is None:
        game_pk_test: list[Any] = []
    else:
        kept = test[test_mask]
        game_pk_test = [game_pks[i] for i in kept]
    return metrics, y_test, p_test, game_pk_test


def _probability_metrics(y_true: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """Primary probability-quality metrics plus secondary ROC-AUC / accuracy."""
    single_class = np.unique(y_true).size < 2
    return {
        "n_test": int(len(y_true)),
        "test_positive_rate": float(np.mean(y_true)) if len(y_true) else float("nan"),
        "degenerate_single_class": bool(single_class),
        # Primary (ADR-003) probability-quality metrics.
        "log_loss": float(log_loss(y_true, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y_true, p, pos_label=1)),
        "ece": float(_expected_calibration_error(y_true, p)),
        # Secondary metrics: reported but never primary.
        "secondary": {
            "roc_auc": None if single_class else float(roc_auc_score(y_true, p)),
            "accuracy": float(accuracy_score(y_true, (p >= 0.5).astype(int))),
        },
    }


def _expected_calibration_error(
    y_true: np.ndarray, p: np.ndarray, n_bins: int = 10
) -> float:
    """Equal-width binned expected calibration error in ``[0, 1]``."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # digitize on the interior edges so probabilities map to bins [0, n_bins-1].
    bin_ids = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    total = len(p)
    ece = 0.0
    for b in range(n_bins):
        mask = bin_ids == b
        count = int(mask.sum())
        if count == 0:
            continue
        confidence = float(p[mask].mean())
        accuracy = float(y_true[mask].mean())
        ece += (count / total) * abs(accuracy - confidence)
    return ece


def _positive_class_proba(estimator: Any, X: np.ndarray) -> np.ndarray:
    """Return ``P(home_win == 1)`` robustly, even for single-class training.

    The shared contract puts ``P(home_win)`` at ``predict_proba`` column 1 when
    both classes were seen in training. If a (degenerate) training partition saw
    only one class, ``predict_proba`` collapses to a single column; map it to the
    correct constant probability rather than crashing.
    """
    proba = estimator.predict_proba(X)
    classes = list(getattr(estimator, "classes_", [0, 1]))
    if 1 not in classes:
        # Positive class never seen in training -> P(home_win) is ~0.
        return np.zeros(proba.shape[0], dtype=float)
    if proba.shape[1] == 1:
        # Only one class present in training columns.
        return np.full(proba.shape[0], 1.0 if classes[0] == 1 else 0.0)
    return proba[:, classes.index(1)].astype(float)


def _drop_unlabeled(
    X: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Drop rows with an undecided (``NaN``) label; cast labels to int."""
    mask = ~np.isnan(y)
    return X[mask], y[mask].astype(int)


def _matrix_rows(matrix: Any) -> list[Mapping[str, Any]]:
    """Accept either a FEAT-004 matrix dict or a bare sequence of rows."""
    if isinstance(matrix, Mapping) and "rows" in matrix:
        return list(matrix["rows"])
    return list(matrix)


def _feature_columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Sorted union of feature keys across all rows (stable schema)."""
    keys: set[str] = set()
    for row in rows:
        features = row.get("features", {}) or {}
        keys.update(features.keys())
    return sorted(keys)


def _row_season(row: Mapping[str, Any]) -> int:
    """Season key for a row: explicit ``season`` if present, else game year."""
    season = row.get("season")
    if season is not None:
        return int(season)
    return int(row["game_date"].year)


def _as_float(value: Any) -> float | None:
    """Coerce a numeric feature value to float; non-numeric -> ``None`` (NaN)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_label(value: Any) -> float | None:
    """Coerce an isolated ``home_win`` target to ``0.0``/``1.0`` (else ``None``)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return float(value)
    return None


def _resolve_build_model(model: Any) -> BuildModel:
    """Return the ``build_model`` callable from a module or pass a callable through."""
    build = getattr(model, "build_model", model)
    if not callable(build):
        raise TypeError("model must be a build_model callable or expose build_model")
    return build


def _model_name(model: Any, build: BuildModel) -> str:
    """Best-effort stable family name for reporting."""
    name = getattr(model, "MODEL_NAME", None)
    if isinstance(name, str) and name:
        return name
    return getattr(build, "__module__", None) or getattr(build, "__name__", "model")
