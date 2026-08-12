"""APP-002 - Streamlit performance dashboard.

Thin display layer only: no business/model/market/journal logic lives here.
All figures are read verbatim from a committed experiment report (ML-005/006/
007/008 evidence) and the OBS-001 prediction journal via
:mod:`app.performance`, which itself does no metric/ROI computation (see that
module's docstring).

Run with:

    streamlit run src/app/performance_page.py

The experiment report path defaults to the repaired-build ranking report
committed at ``reports/experiments/v1-repaired-a910017bac839af5.json``
(ADR-006's evidence); override with ``PERFORMANCE_REPORT_PATH``. The journal
store path defaults to ``state/predictions/journal.jsonl`` (mirrors APP-001's
``state/predictions/daily.jsonl`` convention); override with
``PREDICTION_JOURNAL_PATH``.

2026 holdout note: ML-010 has not completed. This page shows ONLY development
evidence from the repaired 2021-2025 build, explicitly labeled as such -- it
never reads or displays the report's ``holdout_2026`` field.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from app.performance import (
    DEVELOPMENT_EVIDENCE_LABEL,
    MARKET_RELATIVE_NOTE,
    load_calibration_comparison,
    load_model_window_ranking,
    load_prediction_history,
)
from observability.journal import JsonLinesJournalStore

DEFAULT_REPORT_PATH = Path("reports/experiments/v1-repaired-a910017bac839af5.json")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")


def _report_path() -> Path:
    return Path(os.environ.get("PERFORMANCE_REPORT_PATH", DEFAULT_REPORT_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


st.set_page_config(page_title="MLB Model Performance", layout="wide")
st.title("MLB Model Performance")
st.caption(
    f"All model-quality figures below are {DEVELOPMENT_EVIDENCE_LABEL} from the "
    "repaired 2021-2025 build -- the 2026 final holdout evaluation (ML-010) has "
    "not completed and is not shown here."
)

report_path = _report_path()
if not report_path.exists():
    st.info(f"No experiment report found at {report_path}.")
else:
    report = json.loads(report_path.read_text())

    st.subheader("Model x window comparison (log loss / Brier / calibration)")
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

    st.subheader("Calibration / reliability comparison")
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
    st.info(f"No journal found at {journal_path}. Run OBS-001 enrichment first.")
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
