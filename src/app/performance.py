"""APP-002 - data loading/shaping for the Streamlit performance dashboard.

Same contract as ``app.board``: this module does zero probability/metric/ROI
computation. It only reads already-computed numbers from committed experiment
reports (``reports/experiments/*.json``) and the OBS-001 prediction journal
(``src/observability/journal.py``), then selects/labels/sorts fields for
display.

Evidence labeling
-----------------
Development evidence from the repaired 2021-2025 experiment report is always
labeled separately from the ML-010 final 2026 holdout report. The final holdout
loader reads only the committed ``reports/experiments/v1-holdout-2026.json``
shape produced by ``scripts/holdout_2026.py``.

Market-relative metrics / simulated ROI
----------------------------------------
Neither the repaired experiment reports nor the OBS-001 journal enrichment
records carry edge/EV or simulated ROI as persisted evaluation fields.
Computing them here would violate the "load and label only" contract, so they
are deliberately omitted; see ``MARKET_RELATIVE_NOTE``.
"""

from __future__ import annotations

from typing import Any

DEVELOPMENT_EVIDENCE_LABEL = "development evidence (2021-2025 selection/tuning)"
FINAL_HOLDOUT_LABEL = "final 2026 holdout evidence (ML-010)"

MARKET_RELATIVE_NOTE = (
    "Market-relative edge/EV and simulated ROI are not stored as "
    "already-computed fields in the repaired experiment reports, final holdout "
    "report, or OBS-001 journal, so they are omitted here rather than recomputed."
)

# Display order for the calibration variant comparison; matches the variants
# ML-008's calibration report actually emits.
_CALIBRATION_METHOD_ORDER = (
    "uncalibrated",
    "uncalibrated_full_train",
    "sigmoid",
    "isotonic",
)


def load_holdout_summary(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the ML-010 final 2026 holdout report for display.

    Reads already-computed report fields verbatim. Returns a one-row list so it
    can be rendered with the same table-oriented Streamlit path as the other
    dashboard sections.
    """
    metrics = report["metrics"]
    secondary = metrics.get("secondary", {})
    completeness = report.get("gold_feature_completeness", {})
    return [
        {
            "model": report["model"],
            "status": report["status"],
            "build_id": report["build_id"],
            "certification_artifact": report["certification_artifact"],
            "log_loss": metrics["log_loss"],
            "brier": metrics["brier"],
            "ece": metrics["ece"],
            "roc_auc": secondary.get("roc_auc"),
            "accuracy": secondary.get("accuracy"),
            "n_train": metrics["n_train"],
            "n_test": metrics["n_test"],
            "train_seasons": metrics["train_seasons"],
            "test_season": metrics["test_season"],
            "test_positive_rate": metrics["test_positive_rate"],
            "n_rows": report["n_rows"],
            "n_feature_columns": report["n_feature_columns"],
            "excluded_games": len(report.get("excluded_games", [])),
            "feature_completeness": completeness.get("status"),
            "evidence_label": FINAL_HOLDOUT_LABEL,
        }
    ]


def load_holdout_predictions(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape ML-010 game_pk-keyed 2026 predictions for display.

    Prediction probabilities and labels are read verbatim from the report; no
    metric or probability calculation happens here.
    """
    rows = []
    for prediction in report["predictions"]:
        rows.append(
            {
                "game_pk": prediction["game_pk"],
                "p_home_win": prediction["p_home_win"],
                "y_true": prediction["y_true"],
                "evidence_label": FINAL_HOLDOUT_LABEL,
            }
        )
    rows.sort(key=lambda r: r["game_pk"])
    return rows


def load_model_window_ranking(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the repaired report's model x window ranking table for display.

    Reads ``report["ranking"]`` verbatim (already sorted by ML-007's primary
    ordering: log loss -> Brier -> ECE) and labels every row as development
    evidence. Does not read any holdout-result field.
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
    for each calibration method the report contains. Does not read any
    holdout-result field.
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
