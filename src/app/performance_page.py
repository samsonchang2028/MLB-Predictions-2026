"""APP-002 - Streamlit performance dashboard.

Thin display layer only: no business/model/market/journal logic lives here.
All figures are read verbatim from committed experiment reports and the OBS-001
prediction journal via :mod:`app.performance`, which itself does no metric/ROI
computation.

Run with:

    streamlit run src/app/performance_page.py

Defaults:

- ``PERFORMANCE_REPORT_PATH`` ->
  ``reports/experiments/v1-repaired-a910017bac839af5.json``
- ``HOLDOUT_REPORT_PATH`` -> ``reports/experiments/v1-holdout-2026.json``
- ``PREDICTION_JOURNAL_PATH`` -> ``state/predictions/journal.jsonl``
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from app.performance import (
    FINAL_HOLDOUT_LABEL,
    MARKET_RELATIVE_NOTE,
    load_calibration_comparison,
    load_holdout_predictions,
    load_holdout_summary,
    load_model_window_ranking,
    load_prediction_history,
)
from observability.journal import JsonLinesJournalStore

DEFAULT_REPORT_PATH = Path("reports/experiments/v1-repaired-a910017bac839af5.json")
DEFAULT_HOLDOUT_REPORT_PATH = Path("reports/experiments/v1-holdout-2026.json")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")


def _report_path() -> Path:
    return Path(os.environ.get("PERFORMANCE_REPORT_PATH", DEFAULT_REPORT_PATH))


def _holdout_report_path() -> Path:
    return Path(os.environ.get("HOLDOUT_REPORT_PATH", DEFAULT_HOLDOUT_REPORT_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


st.set_page_config(page_title="MLB Model Performance", layout="wide")
st.title("MLB Model Performance")
st.caption(
    "Probability quality is primary: log loss, Brier score, and calibration. "
    "Development/tuning evidence is labeled separately from the final 2026 holdout."
)

holdout_path = _holdout_report_path()
st.subheader("Final 2026 holdout")
if not holdout_path.exists():
    st.info(f"No final holdout report found at {holdout_path}.")
else:
    holdout_report = json.loads(holdout_path.read_text())
    [summary] = load_holdout_summary(holdout_report)
    st.caption(f"Evidence label: {FINAL_HOLDOUT_LABEL}")
    st.dataframe(
        [
            {
                "Model": summary["model"],
                "Build ID": summary["build_id"],
                "Train Seasons": ", ".join(str(s) for s in summary["train_seasons"]),
                "Test Season": summary["test_season"],
                "Log Loss": round(summary["log_loss"], 5),
                "Brier": round(summary["brier"], 5),
                "ECE": round(summary["ece"], 5),
                "ROC-AUC": round(summary["roc_auc"], 4),
                "Accuracy": round(summary["accuracy"], 4),
                "Test Games": summary["n_test"],
                "Gold Completeness": summary["feature_completeness"],
                "Evidence": summary["evidence_label"],
            }
        ],
        use_container_width=True,
        hide_index=True,
    )

    predictions = load_holdout_predictions(holdout_report)
    with st.expander("2026 game-level holdout predictions"):
        st.dataframe(
            [
                {
                    "Game PK": row["game_pk"],
                    "P(home win)": round(row["p_home_win"], 4),
                    "Actual Home Win": bool(row["y_true"]),
                    "Evidence": row["evidence_label"],
                }
                for row in predictions
            ],
            use_container_width=True,
            hide_index=True,
        )

report_path = _report_path()
st.subheader("Development model x window comparison")
if not report_path.exists():
    st.info(f"No development experiment report found at {report_path}.")
else:
    report = json.loads(report_path.read_text())
    ranking_rows = load_model_window_ranking(report)
    st.dataframe(
        [
            {
                "Model": row["model"],
                "Window": row["window"],
                "Log Loss": round(row["log_loss"], 5),
                "Brier": round(row["brier"], 5),
                "ECE": round(row["ece"], 5),
                "ROC-AUC": round(row["roc_auc"], 4),
                "Accuracy": round(row["accuracy"], 4),
                "Test Games": row["n_test"],
                "Evidence": row["evidence_label"],
            }
            for row in ranking_rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Development calibration / reliability comparison")
    calibration_rows = load_calibration_comparison(report)
    st.dataframe(
        [
            {
                "Method": row["method"],
                "Log Loss": round(row["log_loss"], 5),
                "Brier": round(row["brier"], 5),
                "ECE": round(row["ece"], 5),
                "Test Games": row["n_test"],
                "Evidence": row["evidence_label"],
            }
            for row in calibration_rows
        ],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Prediction history")
st.caption(MARKET_RELATIVE_NOTE)
journal_path = _journal_path()
if not journal_path.exists():
    st.info(
        f"No journal found at {journal_path}. Run "
        "`python scripts/enrich_prediction_results.py --date YYYY-MM-DD` after "
        "games finish to create OBS-002 result enrichment."
    )
else:
    history_rows = load_prediction_history(JsonLinesJournalStore(journal_path))
    if not history_rows:
        st.info("No enriched predictions in the journal yet.")
    else:
        st.dataframe(
            [
                {
                    "Game PK": row["game_pk"],
                    "Prediction Time": str(row["prediction_timestamp"]),
                    "Model Version": row["model_version"],
                    "Model P(home)": round(row["model_probability"], 4),
                    "Predicted Home Win": row["predicted_home_win"],
                    "Actual Home Win": row["actual_home_win"],
                    "Correct": row["correct"],
                }
                for row in history_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
