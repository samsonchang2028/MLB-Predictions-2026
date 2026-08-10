"""Model-family x training-window comparison on development folds (ML-007).

Concatenates the expanding (ML-005) and rolling (ML-006) experiment results --
which share an identical schema -- and ranks every ``(model, window)`` combination
by the ADR-003 *primary* probability-quality metrics, pooled across each
combination's test folds.

Fair head-to-head
------------------
The expanding window is evaluated on ``{2022, 2023, 2024, 2025}`` while the
rolling windows only cover ``{2024, 2025}``. Comparing windows on different fold
sets would be apples-to-oranges, so the ranking that *selects a winner* is
computed on the **common** test seasons shared by every compared combination
(the intersection, ``{2024, 2025}`` for the 2021-2025 development span). A
secondary ``full`` view reports each combination's metrics on its own complete
fold set, but the selected winner always comes from the common-season ranking.

Ranking precedence (ADR-003)
-----------------------------
Combinations are ordered by, in strict precedence, lower ``log_loss``, then lower
``brier``, then lower ``ece`` (calibration). ``roc_auc`` and ``accuracy`` are
reported but are *secondary* and never influence the ordering. Ties break stably
by ``(model, window)``. Simulated ROI / market-relative metrics are explicitly
out of scope here (that is later market work; odds live in DATA-009).

2026 holdout
------------
The concatenated inputs already exclude the 2026 final holdout. This module
additionally asserts that no ``fold_metrics`` or ``predictions`` row carries
``test_season == 2026`` and never reads that season, so selection can never be
influenced by the holdout.

The pooled metric math is *not* re-invented: it reuses
:func:`evaluation.runner._probability_metrics` (which in turn uses
``_expected_calibration_error``) verbatim, so pooled log loss / Brier / ECE match
the runner exactly.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from evaluation.runner import _probability_metrics
from experiments.expanding import run_expanding
from experiments.rolling import run_rolling

_HOLDOUT_SEASON = 2026


def compare_windows(
    matrix: Any = None,
    *,
    expanding: dict[str, list[dict[str, Any]]] | None = None,
    rolling: dict[str, list[dict[str, Any]]] | None = None,
    random_state: int = 0,
) -> dict[str, Any]:
    """Rank every ``(model, window)`` combination on the common development folds.

    Parameters
    ----------
    matrix:
        FEAT-004 development matrix. Only used to compute ``expanding`` / ``rolling``
        when those result dicts are not supplied directly.
    expanding:
        Precomputed :func:`experiments.expanding.run_expanding` result. Computed
        from ``matrix`` when ``None``.
    rolling:
        Precomputed :func:`experiments.rolling.run_rolling` result. Computed from
        ``matrix`` when ``None``.
    random_state:
        Seed threaded into ``run_expanding`` / ``run_rolling`` when they are run.

    Returns
    -------
    dict
        ``{"ranking": [...], "best": {...}, "common_test_seasons": [...],
        "full": [...]}`` where ``ranking`` is the winner-selection ordering on the
        common test seasons (best first), ``best`` is ``ranking[0]``,
        ``common_test_seasons`` is the sorted intersection of test seasons across
        all combinations, and ``full`` is the per-combination metrics on each
        combination's own complete fold set. Each combo dict has keys
        ``{model, window, log_loss, brier, ece, roc_auc, accuracy, n_test,
        test_seasons}``.
    """
    if expanding is None:
        expanding = run_expanding(matrix, random_state=random_state)
    if rolling is None:
        rolling = run_rolling(matrix, random_state=random_state)

    fold_metrics = list(expanding["fold_metrics"]) + list(rolling["fold_metrics"])
    predictions = list(expanding["predictions"]) + list(rolling["predictions"])

    _assert_no_holdout(fold_metrics, predictions)

    # Group predictions by (model, window) in a deterministic key order.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in predictions:
        grouped.setdefault((row["model"], row["window"]), []).append(row)
    combos = sorted(grouped)

    # Common test seasons: intersection across every combination's own seasons.
    season_sets = [
        {row["test_season"] for row in grouped[combo]} for combo in combos
    ]
    common_seasons = set.intersection(*season_sets) if season_sets else set()
    common_sorted = sorted(common_seasons)

    # Selection ranking: metrics pooled on the common seasons only (identical folds).
    ranking = [
        _combo_metrics(model, window, grouped[(model, window)], common_seasons)
        for (model, window) in combos
    ]
    ranking.sort(key=_rank_key)

    # Secondary 'full' view: each combination's metrics on its own complete folds.
    full = [
        _combo_metrics(model, window, grouped[(model, window)], seasons=None)
        for (model, window) in combos
    ]

    return {
        "ranking": ranking,
        "best": ranking[0] if ranking else None,
        "common_test_seasons": common_sorted,
        "full": full,
    }


def _combo_metrics(
    model: str,
    window: str,
    rows: list[dict[str, Any]],
    seasons: set[int] | None,
) -> dict[str, Any]:
    """Pooled probability-quality metrics for one combo, optionally season-filtered.

    When ``seasons`` is given, only prediction rows whose ``test_season`` is in that
    set are pooled (fair common-season comparison); when ``None`` the combo's full
    fold set is pooled. Metric math is delegated to the runner's
    :func:`_probability_metrics` so pooled log loss / Brier / ECE match exactly.
    """
    kept = (
        rows if seasons is None else [r for r in rows if r["test_season"] in seasons]
    )
    y_true = np.array([r["y_true"] for r in kept], dtype=int)
    p = np.array([r["p_home_win"] for r in kept], dtype=float)

    metrics = _probability_metrics(y_true, p)
    return {
        "model": model,
        "window": window,
        "log_loss": metrics["log_loss"],
        "brier": metrics["brier"],
        "ece": metrics["ece"],
        "roc_auc": metrics["secondary"]["roc_auc"],
        "accuracy": metrics["secondary"]["accuracy"],
        "n_test": metrics["n_test"],
        "test_seasons": sorted({r["test_season"] for r in kept}),
    }


def _rank_key(combo: dict[str, Any]) -> tuple[float, float, float, str, str]:
    """Primary-metric precedence (log loss, Brier, ECE) with stable tie-break."""
    return (
        combo["log_loss"],
        combo["brier"],
        combo["ece"],
        combo["model"],
        combo["window"],
    )


def _assert_no_holdout(
    fold_metrics: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> None:
    """Guarantee the 2026 final holdout never entered the comparison inputs."""
    if any(row["test_season"] == _HOLDOUT_SEASON for row in fold_metrics):
        raise ValueError(
            f"holdout season {_HOLDOUT_SEASON} present in fold_metrics; "
            "2026 must never be used for selection"
        )
    if any(row["test_season"] == _HOLDOUT_SEASON for row in predictions):
        raise ValueError(
            f"holdout season {_HOLDOUT_SEASON} present in predictions; "
            "2026 must never be used for selection"
        )
