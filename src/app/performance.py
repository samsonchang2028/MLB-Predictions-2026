"""APP-002 - data loading/shaping for the Streamlit performance dashboard.

Same contract as ``app.board``: this module does zero probability/metric/ROI
computation. It only reads already-computed numbers from committed experiment
reports (``reports/experiments/*.json``, produced by ``src/experiments/`` +
``src/evaluation/calibration.py``) and the OBS-001 prediction journal
(``src/observability/journal.py``), then selects/labels/sorts fields for
display.

2026 holdout boundary
----------------------
ML-010 (the untouched 2026 final holdout evaluation) has not completed as of
this task. The repaired experiment report carries a top-level
``holdout_2026`` key -- this module NEVER reads that key. Every row this
module produces from an experiment report is stamped with
``DEVELOPMENT_EVIDENCE_LABEL`` so the page can never present it as final
holdout performance.

Market-relative metrics / simulated ROI
----------------------------------------
Neither the repaired experiment reports nor the OBS-001 journal enrichment
records carry edge/EV or simulated ROI (checked ``src/market/engine.py``:
edge/EV/ROI only exist as return values of pure functions called per-request,
not as a stored, already-computed report or journal field). Computing them
here would violate the "load and label only" contract, so they are
deliberately omitted; see ``MARKET_RELATIVE_NOTE``.
"""

from __future__ import annotations

from typing import Any

DEVELOPMENT_EVIDENCE_LABEL = "development evidence (pre-2026-holdout)"

MARKET_RELATIVE_NOTE = (
    "Market-relative edge/EV and simulated ROI are not stored as "
    "already-computed fields in the repaired experiment reports or the "
    "OBS-001 journal, so they are omitted here rather than recomputed."
)

# Display order for the calibration variant comparison; matches the variants
# ML-008's calibration report actually emits.
_CALIBRATION_METHOD_ORDER = (
    "uncalibrated",
    "uncalibrated_full_train",
    "sigmoid",
    "isotonic",
)


def load_model_window_ranking(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the repaired report's model x window ranking table for display.

    Reads ``report["ranking"]`` verbatim (already sorted by ML-007's primary
    ordering: log loss -> Brier -> ECE) and labels every row as development
    evidence. Never reads ``report["holdout_2026"]``.
    """
    rows = []
    for entry in report["ranking"]:
        rows.append(
            {
                "model": entry["model"],
                "window": entry["window"],
                "log_loss": entry["log_loss"],
                "brier": entry["brier"],
                "ece": entry["ece"],
                "roc_auc": entry["roc_auc"],
                "accuracy": entry["accuracy"],
                "n_test": entry["n_test"],
                "test_seasons": entry["test_seasons"],
                "evidence_label": DEVELOPMENT_EVIDENCE_LABEL,
            }
        )
    return rows


def load_calibration_comparison(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the repaired report's calibration/reliability comparison.

    Reads ``report["calibration"]["variants"][method]["aggregate"]`` verbatim
    for each calibration method the report contains (uncalibrated / sigmoid /
    isotonic). Never reads ``report["holdout_2026"]``.
    """
    variants = report["calibration"]["variants"]
    rows = []
    for method in _CALIBRATION_METHOD_ORDER:
        if method not in variants:
            continue
        aggregate = variants[method]["aggregate"]
        rows.append(
            {
                "method": method,
                "log_loss": aggregate["log_loss"],
                "brier": aggregate["brier"],
                "ece": aggregate["ece"],
                "n_test": aggregate["n_test"],
                "evidence_label": DEVELOPMENT_EVIDENCE_LABEL,
            }
        )
    return rows


def load_prediction_history(store: Any) -> list[dict[str, Any]]:
    """Shape OBS-001 journal enrichment records into display rows.

    ``store`` is anything exposing ``.records()`` (``InMemoryJournalStore`` /
    ``JsonLinesJournalStore``, see :mod:`observability.journal`). Fields are
    read verbatim -- no correctness/probability recomputation happens here;
    ``correct`` is already computed by ``attach_results``.
    """
    rows = []
    for record in store.records():
        rows.append(
            {
                "game_pk": record["game_pk"],
                "prediction_timestamp": record["prediction_timestamp"],
                "model_version": record["model_version"],
                "model_probability": record["model_probability"],
                "predicted_home_win": record["predicted_home_win"],
                "actual_home_win": record["actual_home_win"],
                "correct": record["correct"],
            }
        )
    rows.sort(key=lambda r: (r["game_pk"], str(r["prediction_timestamp"])))
    return rows
