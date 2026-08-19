"""Failure-regime analysis for the ADR-006 locked V1 XGBoost model (ML-013).

Extends ML-012 (feature-family ablation, `src/experiments/ablation.py`) with a
different question: not "which feature family helps", but "where does the
LOCKED model do well or poorly". Slices the model's out-of-sample predictions
on the 2021-2025 development folds by favorite/underdog, actual home/away
outcome, predicted-probability bucket, market-edge bucket, starter-quality
regime, bullpen-workload regime, and season/fold. Reuses the ML-004 runner and
ADR-006 locked hyperparameters exactly as ML-012 does -- no new fold logic, no
new model code, no retuning.

Locked window
-------------
ADR-006 locks the **expanding** window as the V1 production training scheme
(`state/CURRENT.md`: "tuned shallow XGBoost + expanding window + uncalibrated
probabilities"). This module evaluates exactly that configuration -- the same
one ML-011's diagnostics report already uses -- not a new window choice.

Slice-definition choices (documented, not invented silently)
--------------------------------------------------------------
Every slice groups predictions by a real, non-degenerate partition and reports
plain, unmodified ``(p_home_win, y_true)`` metrics within each group (never a
"reframe to the favorite's perspective" transform):

- ``favorite_underdog`` -- games where the model favors home
  (``p_home_win >= 0.5``) vs. favors away. NOTE: a per-game reframing to
  "favorite probability vs. underdog probability" was considered and rejected
  -- log loss/Brier/calibration-gap are mathematically invariant under that
  swap for every game (``p`` <-> ``1-p``, ``y`` <-> ``1-y`` leaves every
  proper scoring rule unchanged), so it would report identical numbers for
  both buckets by construction and carry zero information. Splitting by which
  REAL side the model favors avoids this degeneracy.
- ``home_away_outcome`` -- games actually won by the home team vs. the away
  team (conditioning on the true label, not a relabeling).
- ``probability_bucket`` -- 10 equal-width bins over ``[0, 1]``, same
  convention as ``evaluation.runner._expected_calibration_error``.
- ``edge_bucket`` -- model probability minus no-vig opening-market probability
  (``src/market/engine.py``, reused not reimplemented), for games with a
  MATCHED DATA-009 archive mapping only; games without a resolved market price
  are excluded from this one dimension only (reported as ``n_excluded``).
- ``starter_quality_regime`` / ``bullpen_workload_regime`` -- empirical
  terciles (low/mid/high) of ``diff_starter_season_era_before`` /
  ``diff_bullpen_bullpen_ip_L7`` (home minus away; see `features/build.py`)
  computed over the evaluated rows themselves, rather than a hand-picked
  magic threshold.
- ``season_fold`` -- the expanding fold's ``test_season`` (2022-2025).

Every slice's sample size is reported; any slice under
``LOW_N_THRESHOLD`` (30, matching
``features.completeness.MIN_ROWS_FOR_DISTRIBUTION_CHECKS``) is flagged
``low_confidence`` rather than omitted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from evaluation.runner import _probability_metrics, run_evaluation
from evaluation.splits import expanding_folds
from experiments.ablation import LOCKED_XGBOOST

_HOLDOUT_SEASON = 2026
LOW_N_THRESHOLD = 30

PROBABILITY_BUCKET_EDGES: tuple[float, ...] = tuple(np.linspace(0.0, 1.0, 11))
EDGE_BUCKET_EDGES: tuple[float, ...] = (-1.0, -0.05, 0.0, 0.05, 1.0)
EDGE_BUCKET_LABELS: tuple[str, ...] = (
    "model_much_lower_than_market",
    "model_slightly_lower_than_market",
    "model_slightly_higher_than_market",
    "model_much_higher_than_market",
)


def locked_model_predictions(
    matrix: Any, *, random_state: int = 0
) -> list[dict[str, Any]]:
    """Out-of-sample predictions of the ADR-006 locked XGBoost, expanding window.

    Reuses ``experiments.ablation.LOCKED_XGBOOST`` (ADR-006 hyperparameters,
    hardcoded, never retuned here) and ``evaluation.runner.run_evaluation``
    unmodified. Each returned row is
    ``{game_pk, p_home_win, y_true, test_season}``.
    """
    report = run_evaluation(
        LOCKED_XGBOOST,
        matrix,
        expanding_folds(),
        random_state=random_state,
        model_name="xgboost",
        return_predictions=True,
    )
    predictions: list[dict[str, Any]] = []
    for fold in report["folds"]:
        if fold["test_season"] == _HOLDOUT_SEASON:
            raise ValueError(
                f"holdout season {_HOLDOUT_SEASON} present in failure-regime "
                "predictions; 2026 must never be used for interpretation"
            )
        for row in fold["predictions"]:
            predictions.append({**row, "test_season": fold["test_season"]})
    return predictions


def _rows(matrix: Any) -> list[Mapping[str, Any]]:
    if isinstance(matrix, Mapping) and "rows" in matrix:
        return list(matrix["rows"])
    return list(matrix)


def feature_value_by_game_pk(matrix: Any, column: str) -> dict[int, float | None]:
    """Map ``game_pk -> column`` value (``None`` if missing/non-numeric)."""
    values: dict[int, float | None] = {}
    for row in _rows(matrix):
        value = (row.get("features") or {}).get(column)
        values[row["game_pk"]] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return values


def tercile_labels(values_by_game_pk: Mapping[int, float | None]) -> dict[int, str]:
    """Empirical low/mid/high tercile label per ``game_pk`` (``"missing"`` if absent).

    Cut points are computed from the games that actually have a value, so this
    is a data-driven grouping rather than a hand-picked magic threshold.
    """
    present = {pk: v for pk, v in values_by_game_pk.items() if v is not None}
    if len(present) < 3:
        return {pk: "insufficient_data" for pk in values_by_game_pk}
    ordered = sorted(present.values())
    lo_cut = ordered[len(ordered) // 3]
    hi_cut = ordered[(2 * len(ordered)) // 3]
    labels: dict[int, str] = {}
    for pk, value in values_by_game_pk.items():
        if value is None:
            labels[pk] = "missing"
        elif value < lo_cut:
            labels[pk] = "low_tercile"
        elif value >= hi_cut:
            labels[pk] = "high_tercile"
        else:
            labels[pk] = "mid_tercile"
    return labels


def _slice_metrics(predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    p = np.array([row["p_home_win"] for row in predictions], dtype=float)
    y = np.array([row["y_true"] for row in predictions], dtype=int)
    n = int(len(y))
    if n == 0:
        return {
            "n": 0,
            "avg_predicted_probability": None,
            "actual_win_rate": None,
            "calibration_gap": None,
            "log_loss": None,
            "brier": None,
            "roc_auc": None,
            "low_confidence": True,
        }
    metrics = _probability_metrics(y, p)
    avg_p = float(p.mean())
    actual_rate = float(y.mean())
    return {
        "n": n,
        "avg_predicted_probability": avg_p,
        "actual_win_rate": actual_rate,
        "calibration_gap": abs(avg_p - actual_rate),
        "log_loss": metrics["log_loss"],
        "brier": metrics["brier"],
        "roc_auc": metrics["secondary"]["roc_auc"],
        "low_confidence": n < LOW_N_THRESHOLD,
    }


def slice_by(
    predictions: Sequence[Mapping[str, Any]],
    label_fn: Callable[[Mapping[str, Any]], str | None],
) -> dict[str, Any]:
    """Group ``predictions`` by ``label_fn`` and report metrics per group.

    ``label_fn`` may return ``None`` to exclude a prediction from this
    dimension entirely (used by ``edge_bucket``, where not every game has a
    resolved market price); excluded rows are counted in ``n_excluded``.
    """
    groups: dict[str, list[Mapping[str, Any]]] = {}
    excluded = 0
    for row in predictions:
        label = label_fn(row)
        if label is None:
            excluded += 1
            continue
        groups.setdefault(label, []).append(row)
    return {
        "n_excluded": excluded,
        "buckets": {label: _slice_metrics(rows) for label, rows in sorted(groups.items())},
    }


def _probability_bucket_label(p: float) -> str:
    edges = PROBABILITY_BUCKET_EDGES
    idx = min(int(np.digitize(p, edges[1:-1])), len(edges) - 2)
    return f"{edges[idx]:.2f}-{edges[idx + 1]:.2f}"


def _edge_bucket_label(edge_value: float) -> str:
    edges = EDGE_BUCKET_EDGES
    idx = min(max(int(np.digitize(edge_value, edges[1:-1])), 0), len(EDGE_BUCKET_LABELS) - 1)
    return EDGE_BUCKET_LABELS[idx]


def compute_failure_regimes(
    matrix: Any,
    *,
    market_home_probability_by_game_pk: Mapping[int, float] | None = None,
    random_state: int = 0,
) -> dict[str, Any]:
    """Run the ML-013 failure-regime slices on the ADR-006 locked model.

    ``market_home_probability_by_game_pk`` (optional): ``{game_pk:
    no_vig_opening_market_p_home}`` for games with a resolved MATCHED archive
    mapping (built by the caller via ``src/validation/odds_mapping.py`` +
    ``src/market/engine.no_vig_two_way``, reused read-only, never
    reimplemented). ``edge = p_home_win - market_p_home`` is computed here,
    per prediction; games absent from this mapping are excluded only from the
    ``edge_bucket`` dimension.
    """
    predictions = locked_model_predictions(matrix, random_state=random_state)
    if not predictions:
        raise ValueError("no out-of-sample predictions produced for failure-regime analysis")

    starter_era_diff = feature_value_by_game_pk(matrix, "diff_starter_season_era_before")
    bullpen_ip_diff = feature_value_by_game_pk(matrix, "diff_bullpen_bullpen_ip_L7")
    starter_regime = tercile_labels(starter_era_diff)
    bullpen_regime = tercile_labels(bullpen_ip_diff)
    market_map = market_home_probability_by_game_pk or {}

    dimensions = {
        "favorite_underdog": slice_by(
            predictions,
            lambda row: "model_favors_home" if row["p_home_win"] >= 0.5 else "model_favors_away",
        ),
        "home_away_outcome": slice_by(
            predictions,
            lambda row: "home_win" if row["y_true"] == 1 else "away_win",
        ),
        "probability_bucket": slice_by(
            predictions, lambda row: _probability_bucket_label(row["p_home_win"])
        ),
        "edge_bucket": slice_by(
            predictions,
            lambda row: (
                _edge_bucket_label(row["p_home_win"] - market_map[row["game_pk"]])
                if row["game_pk"] in market_map
                else None
            ),
        ),
        "starter_quality_regime": slice_by(
            predictions, lambda row: starter_regime.get(row["game_pk"], "missing")
        ),
        "bullpen_workload_regime": slice_by(
            predictions, lambda row: bullpen_regime.get(row["game_pk"], "missing")
        ),
        "season_fold": slice_by(predictions, lambda row: str(row["test_season"])),
    }

    return {
        "model": "xgboost",
        "adr": "ADR-006",
        "window": "expanding",
        "n_predictions": len(predictions),
        "low_n_threshold": LOW_N_THRESHOLD,
        "slice_definitions": {
            "favorite_underdog": "model_favors_home if p_home_win>=0.5 else model_favors_away; metrics use raw (p_home_win, y_true), not a favorite-perspective reframing (see module docstring for why that reframing is mathematically degenerate)",
            "home_away_outcome": "actual result: home_win (y_true==1) vs away_win (y_true==0)",
            "probability_bucket": "10 equal-width bins over [0,1], same convention as evaluation.runner ECE",
            "edge_bucket": "model_p_home - no_vig_opening_market_p_home, MATCHED archive games only; games without a resolved price are excluded (see n_excluded)",
            "starter_quality_regime": "empirical tercile of diff_starter_season_era_before (home ERA - away ERA; negative = home starter has the lower/better ERA)",
            "bullpen_workload_regime": "empirical tercile of diff_bullpen_bullpen_ip_L7 (home recent bullpen IP - away recent bullpen IP)",
            "season_fold": "expanding-fold test_season (2022-2025)",
        },
        "dimensions": dimensions,
    }
