"""Feature-family ablation study (ML-012).

Measures, for the ADR-006 locked V1 XGBoost model, how much out-of-sample
probability quality each real Gold feature family contributes. Reuses the
ML-004 walk-forward runner (:func:`evaluation.runner.run_evaluation`) and the
ML-004 fold definitions (:mod:`evaluation.splits`) exactly as the other
``src/experiments`` modules do; this module adds no new fold logic, no new
model code, and no new feature computation.

Family taxonomy (real, not invented)
-------------------------------------
``features.build._COMPONENTS`` is ``("team", "starter", "bullpen")`` and every
Gold column is named ``home_/away_/diff_{component}_{key}``. The FEAT-006
completeness gate (:mod:`features.completeness`) already refines this into the
grouping actually used to report Gold health per family:

- ``team`` / ``starter`` / ``bullpen`` -- the three ``_COMPONENTS`` families
  (pitcher-quality columns only for starter/bullpen), and
- ``rest_schedule`` -- an existing, already-reported cross-component grouping
  covering starter recovery (``days_rest``, ``prev_start_*``) and recent
  bullpen workload (``bullpen_*_prior_*d``), disjoint from the starter/bullpen
  pitcher-quality columns.

:data:`FAMILIES` reuses ``features.completeness.REQUIRED_FAMILY_COLUMNS``
directly instead of inventing a new grouping. On the certified 2021-2025 build
(`a910017bac839af5`) this partitions all 240 feature columns with zero
leftovers (84 team / 75 starter / 45 bullpen / 36 rest_schedule); if a future
build ever produces a column outside this taxonomy, :func:`run_ablation`
raises loudly rather than silently dropping it from every ablation variant.

Ablation design
----------------
With only four families, both incremental and leave-one-group-out are cheap
and each answers a different question, so this module runs both (documented
choice, ML-012 "implementer's choice" clause):

- **leave-one-group-out** -- ``full`` model vs. ``full minus family`` for each
  family. Directly answers "does removing this family hurt?".
- **incremental** -- ``team -> +starter -> +bullpen -> +rest_schedule(=full)``.
  Directly answers "how much does each addition help over what came before?".

The primary model under test is the ADR-006 LOCKED tuned XGBoost
(hyperparameters hardcoded here from the accepted ADR, never retuned by this
study) across all three ADR-003 window strategies (expanding, rolling_2,
rolling_3). Logistic regression and random forest run only as sanity checks
on the cheaper leave-one-out variants over the expanding window (ML-012 scope:
"not a new comparison project").

2026 holdout
------------
Every fold comes from :func:`evaluation.splits.expanding_folds` /
:func:`rolling_folds`, which already exclude ``HOLDOUT_SEASON`` structurally.
:func:`run_ablation` additionally asserts (same pattern as
``experiments.comparison._assert_no_holdout``) that no produced fold-metric row
ever carries ``test_season == 2026`` or a 2026 training season.

Redundancy/leakage scan
------------------------
:func:`_univariate_leakage_scan` computes each column's univariate ROC-AUC
against the label on the certified development rows only (2026 is asserted
absent). This repo's best full-model ROC-AUC is ~0.58-0.59 (see
``reports/experiments/v1-repaired-xgboost-tuning-a910017bac839af5.json``), so
any single column clearing the ``suspicious_auc`` threshold on its own would be
a strong leakage smell in this weak-signal domain. This is a cheap empirical
check that complements, but does not replace, the manual redundancy/leakage
review recorded in the ML-012 markdown report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from evaluation.runner import run_evaluation, vectorize_matrix
from evaluation.splits import expanding_folds, rolling_folds
from features.completeness import REQUIRED_FAMILY_COLUMNS
from models import logistic, random_forest, xgboost_model

_HOLDOUT_SEASON = 2026

# Fixed, deterministic order (matches the natural build order: team, then the
# two pitching components, then the cross-component rest/fatigue grouping).
FAMILIES: tuple[str, ...] = ("team", "starter", "bullpen", "rest_schedule")

# ADR-006 locked V1 hyperparameters. This study measures feature value under
# the LOCKED model; it must never retune, so these are hardcoded constants
# copied verbatim from the accepted ADR, not a config surface.
ADR006_XGBOOST_PARAMS: dict[str, Any] = {
    "max_depth": 2,
    "learning_rate": 0.03,
    "n_estimators": 300,
    "reg_lambda": 10.0,
    "min_child_weight": 3.0,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

_WINDOW_FOLDS: dict[str, Any] = {
    "expanding": expanding_folds,
    "rolling_2": lambda: rolling_folds(2),
    "rolling_3": lambda: rolling_folds(3),
}


class _LockedXGBoost:
    """ADR-006 locked tuned-XGBoost wrapper (mirrors ``scripts/tune_repaired_xgboost.py``'s
    ``_TunedXGBoost`` pattern for driving ``run_evaluation`` with fixed, non-default
    hyperparameters)."""

    MODEL_NAME = "xgboost"

    def build_model(self, random_state: int = 0) -> Any:
        return xgboost_model.build_model(
            random_state=random_state, **ADR006_XGBOOST_PARAMS
        )


LOCKED_XGBOOST = _LockedXGBoost()


def family_columns(feature_columns: Sequence[str]) -> dict[str, list[str]]:
    """Map each real Gold family to the ``feature_columns`` it actually owns.

    Intersects the declared FEAT-006 family column sets with the columns
    actually present in this build (a build may legitimately omit a
    non-diff-eligible column; see ``features.build._diff_eligible``). Any
    column that matches none of the four known families is returned under the
    ``"unclassified"`` key instead of being silently dropped.
    """
    present = set(feature_columns)
    result: dict[str, list[str]] = {}
    claimed: set[str] = set()
    for family in FAMILIES:
        cols = sorted(present & set(REQUIRED_FAMILY_COLUMNS[family]))
        result[family] = cols
        claimed.update(cols)
    unclassified = sorted(present - claimed)
    if unclassified:
        result["unclassified"] = unclassified
    return result


def _matrix_rows(matrix: Any) -> list[Mapping[str, Any]]:
    if isinstance(matrix, Mapping) and "rows" in matrix:
        return list(matrix["rows"])
    return list(matrix)


def _matrix_feature_columns(matrix: Any) -> list[str]:
    if isinstance(matrix, Mapping) and matrix.get("feature_columns"):
        return list(matrix["feature_columns"])
    keys: set[str] = set()
    for row in _matrix_rows(matrix):
        keys.update((row.get("features") or {}).keys())
    return sorted(keys)


def _subset_matrix(matrix: Any, keep_columns: set[str]) -> dict[str, Any]:
    """Shallow-copy ``matrix`` restricted to ``keep_columns`` per row.

    Does NOT recompute Gold feature completeness for the subset. FEAT-006
    completeness is a property of the certified build, not of an ablation
    variant carved out of it (ML-012 constraint: reuse the already-gated
    build); the original matrix's ``feature_completeness``/``build_id``/
    ``certification_status`` are carried through unchanged so
    :func:`evaluation.runner.run_evaluation`'s completeness gate still sees the
    real certified-build PASS rather than a spurious FAIL on a deliberately
    narrowed column set.
    """
    rows = []
    for row in _matrix_rows(matrix):
        features = {
            k: v for k, v in (row.get("features") or {}).items() if k in keep_columns
        }
        new_row = dict(row)
        new_row["features"] = features
        rows.append(new_row)
    if isinstance(matrix, Mapping):
        subset = dict(matrix)
    else:
        subset = {}
    subset["rows"] = rows
    subset["feature_columns"] = sorted(keep_columns)
    return subset


def leave_one_out_variants(
    matrix: Any, families: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """``{"full": matrix, <family>: matrix-without-that-family, ...}``.

    ``full`` and every per-family variant are restricted to the union of the
    four known families' columns, so an ``"unclassified"`` column (should one
    ever appear) cannot silently inflate ``full`` relative to the per-family
    variants.
    """
    all_columns = {c for family in FAMILIES for c in families[family]}
    variants: dict[str, Any] = {"full": _subset_matrix(matrix, all_columns)}
    for family in FAMILIES:
        remove = set(families[family])
        variants[family] = _subset_matrix(matrix, all_columns - remove)
    return variants


def incremental_variants(
    matrix: Any, families: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """Cumulative ``team -> +starter -> +bullpen -> +rest_schedule`` variants.

    The final step's key (``"team+starter+bullpen+rest_schedule"``) covers the
    same columns as ``leave_one_out_variants(...)["full"]``.
    """
    variants: dict[str, Any] = {}
    cumulative: set[str] = set()
    label_parts: list[str] = []
    for family in FAMILIES:
        cumulative |= set(families[family])
        label_parts.append(family)
        variants["+".join(label_parts)] = _subset_matrix(matrix, cumulative)
    return variants


def _fold_metric_rows(
    report: Mapping[str, Any], *, model_name: str, window: str, variant: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fold_report in report["folds"]:
        secondary = fold_report["secondary"]
        rows.append(
            {
                "model": model_name,
                "window": window,
                "variant": variant,
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
    return rows


def _variant_aggregate_row(
    report: Mapping[str, Any], *, model_name: str, window: str, variant: str
) -> dict[str, Any]:
    """Pooled aggregate metrics for one (model, window, variant) combo.

    Uses ``report["aggregate"]`` verbatim -- :func:`evaluation.runner.run_evaluation`
    already pools every fold's labeled test predictions through
    ``evaluation.runner._probability_metrics`` regardless of
    ``return_predictions``. This is NOT re-derived by averaging per-fold ECE
    (ECE is a nonlinear binned statistic; a naive n-weighted average of
    per-fold ECE would not equal the true pooled ECE the way it would for
    log loss / Brier, which are simple per-sample means).
    """
    aggregate = report["aggregate"]
    return {
        "model": model_name,
        "window": window,
        "variant": variant,
        "log_loss": aggregate["log_loss"],
        "brier": aggregate["brier"],
        "ece": aggregate["ece"],
        "roc_auc": aggregate["secondary"]["roc_auc"],
        "accuracy": aggregate["secondary"]["accuracy"],
        "n_test": aggregate["n_test"],
        "n_folds": aggregate["n_folds"],
    }


def _assert_no_holdout(fold_rows: Sequence[Mapping[str, Any]]) -> None:
    """Guarantee the 2026 final holdout never entered any ablation fold."""
    if any(row["test_season"] == _HOLDOUT_SEASON for row in fold_rows):
        raise ValueError(
            f"holdout season {_HOLDOUT_SEASON} present in ablation fold metrics; "
            "2026 must never be used for feature selection"
        )
    if any(_HOLDOUT_SEASON in row["train_seasons"] for row in fold_rows):
        raise ValueError(
            f"holdout season {_HOLDOUT_SEASON} present in ablation train seasons; "
            "2026 must never be used for feature selection"
        )


def _univariate_leakage_scan(
    matrix: Any, *, suspicious_auc: float = 0.65, min_labeled: int = 30
) -> dict[str, Any]:
    """Per-column univariate ROC-AUC vs. the label; flags suspiciously strong columns.

    Runs on every labeled development row (2026 is asserted absent). A column
    could carry signal in either direction, so the reported strength is
    ``max(auc, 1 - auc)``.
    """
    X, y, seasons, feature_columns = vectorize_matrix(matrix)
    labeled = ~np.isnan(y)
    X, y = X[labeled], y[labeled].astype(int)
    seasons_arr = np.asarray(seasons)[labeled]
    if np.any(seasons_arr == _HOLDOUT_SEASON):
        raise ValueError(
            "leakage scan input contains 2026 holdout rows; refusing to score them"
        )

    flagged: list[dict[str, Any]] = []
    scores: dict[str, float | None] = {}
    for j, column in enumerate(feature_columns):
        col = X[:, j]
        mask = ~np.isnan(col)
        if int(mask.sum()) < min_labeled or np.unique(y[mask]).size < 2:
            scores[column] = None
            continue
        auc = float(roc_auc_score(y[mask], col[mask]))
        strength = max(auc, 1.0 - auc)
        scores[column] = strength
        if strength >= suspicious_auc:
            flagged.append({"column": column, "univariate_auc": strength})
    flagged.sort(key=lambda r: -r["univariate_auc"])

    return {
        "n_rows_scanned": int(len(y)),
        "suspicious_auc_threshold": suspicious_auc,
        "flagged_columns": flagged,
        "column_scores": scores,
    }


def run_ablation(
    matrix: Any,
    *,
    random_state: int = 0,
    windows: Sequence[str] = ("expanding", "rolling_2", "rolling_3"),
    sanity_models: Sequence[Any] = (logistic, random_forest),
) -> dict[str, Any]:
    """Run the ML-012 feature-family ablation study.

    Returns
    -------
    dict
        ``{families, primary_model, windows, fold_metrics, variant_aggregates,
        sanity_check_fold_metrics, sanity_check_variant_aggregates,
        leakage_scan}``. ``fold_metrics`` carries one row per
        (window, variant, fold) for the ADR-006 locked XGBoost, ``variant``
        prefixed ``"leave_one_out:*"`` or ``"incremental:*"``.
        ``variant_aggregates`` carries the correctly-pooled (not
        fold-averaged) log loss / Brier / ECE for each (window, variant), one
        row per combination -- this is the primary KEEP/INVESTIGATE/REMOVE
        input. ``sanity_check_*`` carry the same two shapes for logistic
        regression / random forest, leave-one-out only, expanding window only.
    """
    for window in windows:
        if window not in _WINDOW_FOLDS:
            raise ValueError(f"unknown window {window!r}; expected one of {sorted(_WINDOW_FOLDS)}")

    feature_columns = _matrix_feature_columns(matrix)
    families = family_columns(feature_columns)
    if "unclassified" in families:
        raise ValueError(
            "ablation harness found feature columns outside the known "
            f"team/starter/bullpen/rest_schedule taxonomy: {families['unclassified']}"
        )

    loo = leave_one_out_variants(matrix, families)
    inc = incremental_variants(matrix, families)

    fold_rows: list[dict[str, Any]] = []
    aggregate_rows: list[dict[str, Any]] = []
    for window in windows:
        folds = _WINDOW_FOLDS[window]()
        for prefix, variant_map in (("leave_one_out", loo), ("incremental", inc)):
            for variant_name, variant_matrix in variant_map.items():
                report = run_evaluation(
                    LOCKED_XGBOOST,
                    variant_matrix,
                    folds,
                    random_state=random_state,
                    model_name="xgboost",
                )
                variant = f"{prefix}:{variant_name}"
                fold_rows += _fold_metric_rows(
                    report, model_name="xgboost", window=window, variant=variant
                )
                aggregate_rows.append(
                    _variant_aggregate_row(
                        report, model_name="xgboost", window=window, variant=variant
                    )
                )
    _assert_no_holdout(fold_rows)

    sanity_rows: list[dict[str, Any]] = []
    sanity_aggregate_rows: list[dict[str, Any]] = []
    expanding = expanding_folds()
    for module in sanity_models:
        for variant_name, variant_matrix in loo.items():
            report = run_evaluation(
                module, variant_matrix, expanding, random_state=random_state
            )
            variant = f"leave_one_out:{variant_name}"
            sanity_rows += _fold_metric_rows(
                report, model_name=report["model"], window="expanding", variant=variant
            )
            sanity_aggregate_rows.append(
                _variant_aggregate_row(
                    report, model_name=report["model"], window="expanding", variant=variant
                )
            )
    _assert_no_holdout(sanity_rows)

    leakage_scan = _univariate_leakage_scan(matrix)

    return {
        "families": families,
        "primary_model": {
            "name": "xgboost",
            "params": dict(ADR006_XGBOOST_PARAMS),
            "adr": "ADR-006",
        },
        "windows": list(windows),
        "fold_metrics": fold_rows,
        "variant_aggregates": aggregate_rows,
        "sanity_check_fold_metrics": sanity_rows,
        "sanity_check_variant_aggregates": sanity_aggregate_rows,
        "leakage_scan": leakage_scan,
    }
