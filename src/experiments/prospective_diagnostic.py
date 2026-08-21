"""ML-015 - prospective model and market diagnostic study.

Diagnoses the first week of REAL, LIVE production predictions (not a
backtest) made by the ADR-006 locked model, without retuning it. Reuses
already-existing, unmodified pipeline code end to end rather than
re-deriving any of it:

- ``app.board.load_daily_board_with_diagnostics`` for the exact same
  latest-prediction-per-game join between the immutable prediction store
  (``pipelines.daily``) and the OBS-001/OBS-002 result journal
  (``observability.journal``) the live dashboard itself uses, including its
  PLAY/PASS threshold (``DEFAULT_EDGE_THRESHOLD``). This module never
  redefines that threshold or the join/dedup logic.
- ``evaluation.runner._probability_metrics`` for log loss / Brier / ECE /
  ROC-AUC / accuracy -- the identical formulas the ADR-003 primary-metric
  policy and the 2026 holdout report already use.
- ``experiments.failure_regimes.slice_by`` / ``feature_value_by_game_pk`` /
  ``tercile_labels`` for the failure-regime slice machinery, so this
  prospective study's bucket/tercile conventions match ML-013's exactly.

Three-system distinction (see ``src/app/board.py``'s module docstring):
probability model (raw ``model_probability`` = P(home_win)) vs. market
engine (``model_probability - market_probability`` = edge) vs. betting
selection layer (PLAY/PASS, a synthetic display threshold). Part 1/3 use the
raw home-win probability; Parts 2/4 use the *selected-side* probability
(``model_chance``/``market_chance``, already computed by
``load_daily_board_with_diagnostics`` -- the home-side value if edge>=0,
else the away-side value) since PLAY/PASS and edge are about the picked
side, not the raw home-win call. Part 5 uses the raw home-win framing so its
numbers are directly comparable to ML-013's.

No ROI: neither ``daily.jsonl`` nor the OBS-001 journal record a stake/wager
amount (see ``app.performance.MARKET_RELATIVE_NOTE`` -- the same
already-documented repo-wide finding), so no ROI figure is fabricated here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import numpy as np

from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    SKIP_NO_STARTER_ANNOUNCED,
    _parse_datetime,
    _prediction_key,
    load_daily_board_with_diagnostics,
)
from evaluation.runner import _probability_metrics
from experiments.failure_regimes import (
    LOW_N_THRESHOLD,
    feature_value_by_game_pk,
    slice_by,
    tercile_labels,
)

NEAR_FIFTY_BAND = 0.05  # |p_home_win - 0.5| < this => "near_50"
PROBABILITY_BUCKET_EDGES: tuple[float, ...] = (0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0)
EDGE_BUCKET_EDGES_PCT: tuple[float, ...] = (0.0, 2.0, 4.0, 6.0, 8.0, 100.0)
EDGE_BUCKET_LABELS: tuple[str, ...] = ("0-2%", "2-4%", "4-6%", "6-8%", "8%+")

ROI_NOTE = (
    "no stake/wager amount field exists in daily.jsonl or the OBS-001 "
    "journal (see app.performance.MARKET_RELATIVE_NOTE, the same repo-wide "
    "finding); ROI is not computed here rather than fabricated."
)


class _ListStore:
    """Read-only ``.records()`` adapter over an already-immutable JSONL list.

    ``app.board``'s loaders only need ``.records()``; going through the real
    append-only stores' idempotency/conflict machinery here would be
    unnecessary re-validation of data that is already the immutable JSONL
    contents.
    """

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._records = list(records)

    def records(self) -> list[Mapping[str, Any]]:
        return self._records


# --------------------------------------------------------------------------- #
# Row assembly
# --------------------------------------------------------------------------- #
def resolved_rows(
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    *,
    model_version: str | None = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> tuple[list[dict[str, Any]], int]:
    """Board rows (latest prediction per game) resolved to a Final result.

    Reuses ``app.board.load_daily_board_with_diagnostics`` for the join,
    dedup, PLAY/PASS labeling, and selected-side probability computation.
    Returns ``(rows, n_malformed_daily_board_skips)``.
    """
    board = load_daily_board_with_diagnostics(
        _ListStore(daily_records),
        journal_store=_ListStore(journal_records),
        edge_threshold=edge_threshold,
    )
    rows = [dict(r) for r in board["rows"] if r.get("result_status") == "Final"]
    if model_version is not None:
        rows = [r for r in rows if r.get("model_version") == model_version]
    return rows, len(board["skipped"])


def attach_raw_prediction_fields(
    rows: Sequence[Mapping[str, Any]], daily_records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Augment board rows with fields the board intentionally omits (raw
    American odds of the selected side, hours-to-first-pitch), joined back to
    the immutable ``daily.jsonl`` prediction record on the same
    ``(game_pk, prediction_timestamp)`` key ``app.board`` itself uses.
    """
    by_key = {
        _prediction_key(r.get("game_pk"), r.get("prediction_timestamp")): r
        for r in daily_records
    }
    augmented: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        raw = by_key.get(_prediction_key(row.get("game_pk"), row.get("prediction_timestamp")))
        selected_american = None
        hours_to_first_pitch = None
        if raw is not None:
            selected_american = (
                raw.get("home_american") if row.get("pick_side") == "home" else raw.get("away_american")
            )
            game_start = _parse_datetime(raw.get("game_start_timestamp"))
            prediction_ts = row.get("prediction_timestamp")
            if game_start is not None and isinstance(prediction_ts, datetime):
                hours_to_first_pitch = (game_start - prediction_ts).total_seconds() / 3600.0
        row["selected_side_american"] = selected_american
        row["hours_to_first_pitch"] = hours_to_first_pitch
        augmented.append(row)
    return augmented


def _as_predictions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Adapt board rows into the ``{p_home_win, y_true, game_pk}`` shape
    ``experiments.failure_regimes.slice_by`` expects."""
    return [
        {
            "p_home_win": float(row["model_probability"]),
            "y_true": 1 if row["actual_home_win"] else 0,
            "game_pk": row["game_pk"],
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Part 1 - all resolved eligible predictions
# --------------------------------------------------------------------------- #
def all_predictions_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "avg_predicted_home_probability": None,
            "actual_home_win_rate": None,
            "calibration_gap": None,
            "log_loss": None,
            "brier": None,
            "ece": None,
            "roc_auc": None,
            "accuracy": None,
            "low_confidence": True,
        }
    p = np.array([row["model_probability"] for row in rows], dtype=float)
    y = np.array([1 if row["actual_home_win"] else 0 for row in rows], dtype=int)
    metrics = _probability_metrics(y, p)
    avg_p = float(p.mean())
    actual_rate = float(y.mean())
    return {
        "n": n,
        "avg_predicted_home_probability": avg_p,
        "actual_home_win_rate": actual_rate,
        "calibration_gap": abs(avg_p - actual_rate),
        "log_loss": metrics["log_loss"],
        "brier": metrics["brier"],
        "ece": metrics["ece"],
        "roc_auc": metrics["secondary"]["roc_auc"],
        "accuracy": metrics["secondary"]["accuracy"],
        "low_confidence": n < LOW_N_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# Part 2 - PLAY subset
# --------------------------------------------------------------------------- #
def play_subset_summary(
    rows: Sequence[Mapping[str, Any]], *, edge_threshold: float = DEFAULT_EDGE_THRESHOLD
) -> dict[str, Any]:
    play_rows = [row for row in rows if row.get("play")]
    n = len(play_rows)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "edge_threshold": edge_threshold,
            "avg_selected_side_model_probability": None,
            "actual_selected_side_win_rate": None,
            "avg_market_probability_selected_side": None,
            "avg_abs_edge": None,
            "avg_selected_side_american_odds": None,
            "model_vs_actual_gap": None,
            "roi": None,
            "roi_note": ROI_NOTE,
            "low_confidence": True,
        }
    wins = sum(1 for row in play_rows if row["correct"])
    win_rate = wins / n
    avg_model_p = float(np.mean([row["model_chance"] for row in play_rows]))
    avg_market_p = float(np.mean([row["market_chance"] for row in play_rows]))
    avg_abs_edge = float(np.mean([abs(row["edge"]) for row in play_rows]))
    odds_values = [
        row["selected_side_american"] for row in play_rows if row.get("selected_side_american") is not None
    ]
    avg_odds = float(np.mean(odds_values)) if odds_values else None
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": win_rate,
        "edge_threshold": edge_threshold,
        "avg_selected_side_model_probability": avg_model_p,
        "actual_selected_side_win_rate": win_rate,
        "avg_market_probability_selected_side": avg_market_p,
        "avg_abs_edge": avg_abs_edge,
        "avg_selected_side_american_odds": avg_odds,
        "model_vs_actual_gap": avg_model_p - win_rate,
        "roi": None,
        "roi_note": ROI_NOTE,
        "low_confidence": n < LOW_N_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# Part 3 - probability buckets (raw P(home_win), same framing as Part 1)
# --------------------------------------------------------------------------- #
def _probability_bucket_label(p: float) -> str:
    edges = PROBABILITY_BUCKET_EDGES
    idx = min(int(np.digitize(p, edges[1:-1])), len(edges) - 2)
    lo, hi = edges[idx], edges[idx + 1]
    if idx == 0:
        return f"<{hi * 100:.0f}%"
    if idx == len(edges) - 2:
        return f">={lo * 100:.0f}%"
    return f"{lo * 100:.0f}-{hi * 100:.0f}%"


def probability_bucket_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return slice_by(_as_predictions(rows), lambda row: _probability_bucket_label(row["p_home_win"]))


# --------------------------------------------------------------------------- #
# Part 4 - edge buckets (selected-side probabilities, |model - market| edge)
# --------------------------------------------------------------------------- #
def _edge_bucket_label(abs_edge_pct: float) -> str:
    edges = EDGE_BUCKET_EDGES_PCT
    idx = min(int(np.digitize(abs_edge_pct, edges[1:-1])), len(EDGE_BUCKET_LABELS) - 1)
    return EDGE_BUCKET_LABELS[idx]


def _edge_bucket_group_metrics(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(group)
    if n == 0:
        return {
            "n": 0,
            "avg_model_probability_selected_side": None,
            "avg_market_probability_selected_side": None,
            "actual_win_rate": None,
            "avg_edge": None,
            "roi": None,
            "roi_note": ROI_NOTE,
            "low_confidence": True,
        }
    wins = sum(1 for row in group if row["correct"])
    return {
        "n": n,
        "avg_model_probability_selected_side": float(np.mean([row["model_chance"] for row in group])),
        "avg_market_probability_selected_side": float(np.mean([row["market_chance"] for row in group])),
        "actual_win_rate": wins / n,
        "avg_edge": float(np.mean([abs(row["edge"]) for row in group])),
        "roi": None,
        "roi_note": ROI_NOTE,
        "low_confidence": n < LOW_N_THRESHOLD,
    }


def edge_bucket_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {label: [] for label in EDGE_BUCKET_LABELS}
    for row in rows:
        groups[_edge_bucket_label(abs(row["edge"]) * 100)].append(row)
    return {label: _edge_bucket_group_metrics(group) for label, group in groups.items()}


# --------------------------------------------------------------------------- #
# Part 5 - failure regimes (raw P(home_win) framing, ML-013 slice conventions)
# --------------------------------------------------------------------------- #
def _time_to_first_pitch_label(hours: float | None) -> str | None:
    if hours is None:
        return None  # excluded from this dimension only (see n_excluded)
    if hours < 2:
        return "<2h"
    if hours < 6:
        return "2-6h"
    if hours < 24:
        return "6-24h"
    return "24h+"


def failure_regime_slices(
    rows: Sequence[Mapping[str, Any]], game_features_records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    predictions = _as_predictions(rows)
    pick_side = {row["game_pk"]: row.get("pick_side") for row in rows}
    edge_map = {row["game_pk"]: abs(row.get("edge", 0.0)) for row in rows}
    ttfp_map = {row["game_pk"]: row.get("hours_to_first_pitch") for row in rows}

    starter_regime = tercile_labels(
        feature_value_by_game_pk(game_features_records, "diff_starter_season_era_before")
    )
    bullpen_regime = tercile_labels(
        feature_value_by_game_pk(game_features_records, "diff_bullpen_bullpen_ip_L7")
    )

    dimensions = {
        "favorite_underdog": slice_by(
            predictions, lambda row: "model_favors_home" if row["p_home_win"] >= 0.5 else "model_favors_away"
        ),
        "home_away_selection": slice_by(
            predictions, lambda row: pick_side.get(row["game_pk"])
        ),
        "home_away_outcome": slice_by(
            predictions, lambda row: "home_win" if row["y_true"] == 1 else "away_win"
        ),
        "near_50_vs_confident": slice_by(
            predictions,
            lambda row: "near_50" if abs(row["p_home_win"] - 0.5) < NEAR_FIFTY_BAND else "confident",
        ),
        "high_edge_vs_low_edge": slice_by(
            predictions,
            lambda row: (
                "high_edge_play" if edge_map.get(row["game_pk"], 0.0) >= DEFAULT_EDGE_THRESHOLD else "low_edge_pass"
            ),
        ),
        "starter_quality_regime": slice_by(
            predictions, lambda row: starter_regime.get(row["game_pk"], "missing")
        ),
        "bullpen_workload_regime": slice_by(
            predictions, lambda row: bullpen_regime.get(row["game_pk"], "missing")
        ),
        "time_to_first_pitch": slice_by(
            predictions, lambda row: _time_to_first_pitch_label(ttfp_map.get(row["game_pk"]))
        ),
    }
    skipped_dimensions = {
        "sportsbook": (
            "every canonical journal prediction is pinned to a single sportsbook "
            "(draftkings via the_odds_api; see PIPE-004); no cross-book variation "
            "exists in this data to slice by."
        ),
        "starter_uncertainty_missing_late_starter": (
            "games with no announced starter are excluded upstream before a "
            "prediction row is ever written (state/predictions/skipped.jsonl, "
            "reason=no_starter_announced); they cannot appear in the resolved-"
            "prediction set by construction. starter_quality_regime above is the "
            "nearest available proxy (ML-013's starter-ERA-differential tercile); "
            "see data_quality.starter_uncertainty_skip_count for the aggregate "
            "coverage count instead."
        ),
    }
    return {
        "dimensions": dimensions,
        "skipped_dimensions": skipped_dimensions,
        "near_fifty_band": NEAR_FIFTY_BAND,
        "high_edge_threshold": DEFAULT_EDGE_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# Historical comparison (comparison-only; never a selection input)
# --------------------------------------------------------------------------- #
def historical_comparison(part1: Mapping[str, Any], holdout_report: Mapping[str, Any]) -> dict[str, Any]:
    """Delta of Part 1's metrics against the locked 2026 holdout report.

    Pure/read-only: reads ``holdout_report`` verbatim for display, never
    mutates ``part1`` or feeds the holdout values back into any prospective
    computation.
    """
    holdout_metrics = holdout_report["metrics"]
    holdout_secondary = holdout_metrics.get("secondary", {})
    fields = {
        "log_loss": (part1.get("log_loss"), holdout_metrics.get("log_loss")),
        "brier": (part1.get("brier"), holdout_metrics.get("brier")),
        "ece": (part1.get("ece"), holdout_metrics.get("ece")),
        "roc_auc": (part1.get("roc_auc"), holdout_secondary.get("roc_auc")),
        "accuracy": (part1.get("accuracy"), holdout_secondary.get("accuracy")),
    }
    deltas = {}
    for name, (prospective, holdout) in fields.items():
        delta = None if prospective is None or holdout is None else prospective - holdout
        deltas[name] = {"prospective": prospective, "holdout_2026": holdout, "delta": delta}
    return {"n_holdout": holdout_metrics.get("n_test"), "metrics": deltas}


# --------------------------------------------------------------------------- #
# Top-level report assembly
# --------------------------------------------------------------------------- #
def build_report(
    *,
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    game_features_records: Sequence[Mapping[str, Any]],
    skipped_records: Sequence[Mapping[str, Any]] = (),
    model_version: str | None = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> dict[str, Any]:
    """Assemble all 5 ML-015 analysis parts. Never reads the 2026 holdout --
    see :func:`historical_comparison` for the separate, display-only
    comparison step."""
    rows, n_malformed = resolved_rows(
        daily_records, journal_records, model_version=model_version, edge_threshold=edge_threshold
    )
    rows = attach_raw_prediction_fields(rows, daily_records)

    starter_skips = sum(1 for row in skipped_records if row.get("reason") == SKIP_NO_STARTER_ANNOUNCED)

    return {
        "n_resolved_eligible_predictions": len(rows),
        "edge_threshold": edge_threshold,
        "model_version_filter": model_version,
        "part1_all_predictions": all_predictions_summary(rows),
        "part2_play_subset": play_subset_summary(rows, edge_threshold=edge_threshold),
        "part3_probability_buckets": probability_bucket_table(rows),
        "part4_edge_buckets": edge_bucket_table(rows),
        "part5_failure_regimes": failure_regime_slices(rows, game_features_records),
        "data_quality": {
            "daily_board_malformed_skips": n_malformed,
            "starter_uncertainty_skip_count": starter_skips,
        },
    }
