"""Deeper Gold feature redundancy analysis (ML-013), beyond ML-012's univariate scan.

ML-012 (`src/experiments/ablation.py`) already ran a per-column univariate
ROC-AUC-vs-target leakage scan. This module answers four different questions
ML-012 did not cover, reusing ML-012's family taxonomy and the certified,
already completeness-gated Gold build (never recomputed here):

1. :func:`pairwise_correlation_scan` -- pairwise/group correlation AMONG Gold
   feature columns (not vs. the target).
2. :func:`missingness_report` -- per-column missingness, read directly from
   the certified build's own FEAT-006 completeness report (not recomputed).
3. :func:`per_season_coverage_stability` -- whether a column's population
   rate is stable across the five development seasons.
4. :func:`ablation_fold_concentration` -- re-reads ML-012's own
   `reports/experiments/ml-012-feature-ablation.json` `fold_metrics` (ablation
   itself is never rerun here) to check whether each family's leave-one-out
   log-loss benefit is spread across folds or concentrated in one.

Correlation-scan simplification (documented, not silent)
----------------------------------------------------------
:func:`pairwise_correlation_scan` mean-imputes each column before computing
Pearson correlation. This is a purely DESCRIPTIVE redundancy scan (it never
feeds a model or a selection decision), so the point-in-time/train-only
preprocessing rules that apply to model fitting do not apply here; mean
imputation only needs to be good enough to reveal linear redundancy between
columns, not to be leakage-safe training data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from evaluation.runner import vectorize_matrix
from experiments.ablation import FAMILIES

_HOLDOUT_SEASON = 2026


def _assert_no_holdout(seasons: Sequence[int]) -> None:
    if any(s == _HOLDOUT_SEASON for s in seasons):
        raise ValueError(
            f"redundancy analysis input contains {_HOLDOUT_SEASON} holdout rows; "
            "2026 must never be used for interpretation"
        )


# --------------------------------------------------------------------------- #
# 1. Pairwise / group correlation among feature columns
# --------------------------------------------------------------------------- #
def pairwise_correlation_scan(
    matrix: Any, *, threshold: float = 0.9
) -> dict[str, Any]:
    """Flag column pairs with ``|Pearson correlation| >= threshold`` and group them.

    Groups are connected components of the flagged-pair graph (union-find):
    a "near-duplicate family" is more informative than a flat pair list when
    three or more columns are all mutually redundant.
    """
    X, _y, seasons, feature_columns = vectorize_matrix(matrix)
    _assert_no_holdout(seasons)

    col_means = np.nanmean(X, axis=0)
    col_means = np.where(np.isnan(col_means), 0.0, col_means)
    imputed = np.where(np.isnan(X), col_means, X)
    stds = imputed.std(axis=0)
    keep_mask = stds > 0
    kept_columns = [c for c, keep in zip(feature_columns, keep_mask) if keep]
    dropped_constant = [c for c, keep in zip(feature_columns, keep_mask) if not keep]

    corr = np.corrcoef(imputed[:, keep_mask], rowvar=False)
    n = len(kept_columns)
    pairs: list[dict[str, Any]] = []
    for i in range(n):
        for j in range(i + 1, n):
            r = float(corr[i, j])
            if abs(r) >= threshold:
                pairs.append(
                    {"column_a": kept_columns[i], "column_b": kept_columns[j], "correlation": r}
                )
    pairs.sort(key=lambda p: -abs(p["correlation"]))

    groups = _connected_components(kept_columns, pairs)

    return {
        "threshold": threshold,
        "n_columns_scanned": n,
        "n_constant_columns_excluded": len(dropped_constant),
        "constant_columns_excluded": dropped_constant,
        "n_pairs_scanned": n * (n - 1) // 2,
        "n_flagged_pairs": len(pairs),
        "flagged_pairs": pairs,
        "near_duplicate_groups": groups,
    }


def _connected_components(
    columns: Sequence[str], pairs: Sequence[Mapping[str, Any]]
) -> list[list[str]]:
    """Group columns transitively linked by a flagged correlation pair."""
    parent: dict[str, str] = {c: c for c in columns}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for pair in pairs:
        union(pair["column_a"], pair["column_b"])

    members: dict[str, list[str]] = {}
    for c in columns:
        members.setdefault(find(c), []).append(c)

    groups = [sorted(group) for group in members.values() if len(group) > 1]
    groups.sort(key=lambda g: (-len(g), g))
    return groups


# --------------------------------------------------------------------------- #
# 2. Missingness (reused directly from the certified FEAT-006 report)
# --------------------------------------------------------------------------- #
def missingness_report(matrix: Any) -> dict[str, Any]:
    """Per-column missingness, read from the build's own completeness report.

    Reuses ``matrix["feature_completeness"]["columns"][col]["null_rate"]``
    verbatim -- the certified build already computed this (FEAT-006); this
    function does not recompute it.
    """
    if not isinstance(matrix, Mapping) or not isinstance(
        matrix.get("feature_completeness"), Mapping
    ):
        raise ValueError(
            "redundancy missingness report requires a certified build's "
            "feature_completeness report (matrix['feature_completeness'])"
        )
    columns = matrix["feature_completeness"]["columns"]
    any_missing = sorted(
        (
            {"column": col, "null_rate": report["null_rate"], "family": report["family"]}
            for col, report in columns.items()
            if report["null_rate"] > 0
        ),
        key=lambda r: -r["null_rate"],
    )
    return {
        "n_columns": len(columns),
        "n_columns_with_any_missing": len(any_missing),
        "columns_with_any_missing": any_missing,
    }


# --------------------------------------------------------------------------- #
# 3. Per-season feature coverage stability
# --------------------------------------------------------------------------- #
def per_season_coverage_stability(
    matrix: Any, *, unstable_spread_threshold: float = 0.2
) -> dict[str, Any]:
    """Flag columns whose non-null population rate swings across seasons.

    ``spread`` is ``max(season population rate) - min(season population
    rate)``; a column required at nearly 100% every season but that drops out
    in one season would surface here even though its whole-build population
    rate (FEAT-006) looks fine on average.
    """
    X, _y, seasons, feature_columns = vectorize_matrix(matrix)
    _assert_no_holdout(seasons)
    seasons_arr = np.asarray(seasons)
    unique_seasons = sorted(set(int(s) for s in seasons_arr.tolist()))

    populated = ~np.isnan(X)
    rate_rows = []
    for season in unique_seasons:
        mask = seasons_arr == season
        rate_rows.append(populated[mask].mean(axis=0))
    rate_matrix = np.stack(rate_rows)  # (n_seasons, n_columns)
    spread = rate_matrix.max(axis=0) - rate_matrix.min(axis=0)

    unstable: list[dict[str, Any]] = []
    for j, column in enumerate(feature_columns):
        if spread[j] > unstable_spread_threshold:
            unstable.append(
                {
                    "column": column,
                    "spread": float(spread[j]),
                    "by_season": {
                        str(s): float(rate_matrix[i, j]) for i, s in enumerate(unique_seasons)
                    },
                }
            )
    unstable.sort(key=lambda r: -r["spread"])

    return {
        "seasons": unique_seasons,
        "unstable_spread_threshold": unstable_spread_threshold,
        "n_unstable_columns": len(unstable),
        "unstable_columns": unstable,
    }


# --------------------------------------------------------------------------- #
# 4. Fold-concentration of ML-012's per-family ablation benefit
# --------------------------------------------------------------------------- #
def ablation_fold_concentration(
    ml012_report: Mapping[str, Any],
    *,
    families: Sequence[str] = FAMILIES,
    windows: Sequence[str] = ("expanding", "rolling_2", "rolling_3"),
    concentration_threshold: float = 0.5,
) -> dict[str, Any]:
    """Is each family's ML-012 leave-one-out log-loss benefit spread or concentrated?

    Re-reads ``ml012_report["fold_metrics"]`` (already generated by ML-012;
    ablation is never rerun here). For each ``(family, window)``, computes the
    per-fold log-loss delta between the ``leave_one_out:full`` and
    ``leave_one_out:<family>`` variants on the SAME fold, and reports whether
    one fold's ``|delta|`` accounts for more than ``concentration_threshold``
    of the total ``|delta|`` across that window's folds.
    """
    fold_metrics = ml012_report["fold_metrics"]
    by_key: dict[tuple[str, str, int], Mapping[str, Any]] = {
        (row["window"], row["variant"], row["test_season"]): row for row in fold_metrics
    }

    results: dict[str, Any] = {}
    for family in families:
        per_window: dict[str, Any] = {}
        for window in windows:
            deltas: list[dict[str, Any]] = []
            for row in fold_metrics:
                if row["window"] != window or row["variant"] != "leave_one_out:full":
                    continue
                season = row["test_season"]
                removed = by_key.get((window, f"leave_one_out:{family}", season))
                if removed is None:
                    continue
                deltas.append(
                    {
                        "test_season": season,
                        "log_loss_delta": removed["log_loss"] - row["log_loss"],
                        "n_test": row["n_test"],
                    }
                )
            if not deltas:
                continue
            abs_deltas = [abs(d["log_loss_delta"]) for d in deltas]
            total_abs = sum(abs_deltas)
            max_share = (max(abs_deltas) / total_abs) if total_abs else 0.0
            per_window[window] = {
                "per_fold_deltas": deltas,
                "mean_delta": sum(d["log_loss_delta"] for d in deltas) / len(deltas),
                "max_abs_delta": max(abs_deltas),
                "max_fold_share_of_total_abs_delta": max_share,
                "concentrated_in_one_fold": max_share > concentration_threshold,
            }
        results[family] = per_window

    return {"concentration_threshold": concentration_threshold, "families": results}
