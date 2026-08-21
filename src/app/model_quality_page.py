"""Model quality dashboard — historical vs prospective probability evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from app.dashboard_analytics import (
    DashboardPaths,
    HISTORICAL_EVIDENCE_LABEL,
    PROSPECTIVE_EVIDENCE_LABEL,
    build_prospective_model_quality,
    read_jsonl,
)
from app.performance import (
    FINAL_HOLDOUT_LABEL,
    load_calibration_comparison,
    load_holdout_summary,
    load_model_window_ranking,
)

DEFAULT_HOLDOUT_REPORT_PATH = Path("reports/experiments/v1-holdout-2026.json")
DEFAULT_DEVELOPMENT_REPORT_PATH = Path(
    "reports/experiments/v1-repaired-a910017bac839af5.json"
)


def _paths() -> DashboardPaths:
    return DashboardPaths(
        predictions=Path(os.environ.get("PREDICTIONS_STORE_PATH", DashboardPaths.predictions)),
        journal=Path(os.environ.get("PREDICTION_JOURNAL_PATH", DashboardPaths.journal)),
        holdout_report=Path(os.environ.get("HOLDOUT_REPORT_PATH", DEFAULT_HOLDOUT_REPORT_PATH)),
        development_report=Path(
            os.environ.get("PERFORMANCE_REPORT_PATH", DEFAULT_DEVELOPMENT_REPORT_PATH)
        ),
    )


def _render_metric_block(title: str, metrics: dict | None, *, sample_label: str) -> None:
    st.markdown(f"**{title}**")
    if metrics is None:
        st.info(f"No resolved predictions yet for {sample_label.lower()}.")
        return
    cols = st.columns(5)
    cols[0].metric("Log loss", round(metrics["log_loss"], 4))
    cols[1].metric("Brier", round(metrics["brier"], 4))
    cols[2].metric("Calibration (ECE)", round(metrics["ece"], 4))
    cols[3].metric("ROC-AUC", "—" if metrics["roc_auc"] is None else round(metrics["roc_auc"], 4))
    cols[4].metric("Sample size (N)", metrics["n"])
    st.caption(f"{sample_label}. Small N can move quickly — avoid over-reading short windows.")


st.set_page_config(page_title="Model Quality", layout="wide")
st.title("Model Quality")
st.caption(
    "Probability quality is primary: log loss, Brier score, and calibration. "
    "Historical evaluation and prospective production monitoring are shown separately."
)

paths = _paths()

st.subheader(HISTORICAL_EVIDENCE_LABEL)
st.caption("Locked development and final holdout reports — not live betting results.")

holdout_path = paths.holdout_report
if not holdout_path.exists():
    st.info(f"No final holdout report found at {holdout_path}.")
else:
    holdout_report = json.loads(holdout_path.read_text(encoding="utf-8"))
    [summary] = load_holdout_summary(holdout_report)
    st.markdown(f"**Final 2026 holdout** · {FINAL_HOLDOUT_LABEL}")
    st.dataframe(
        [
            {
                "Log Loss": round(summary["log_loss"], 5),
                "Brier": round(summary["brier"], 5),
                "ECE": round(summary["ece"], 5),
                "ROC-AUC": round(summary["roc_auc"], 4),
                "Accuracy": round(summary["accuracy"], 4),
                "N": summary["n_test"],
                "Evidence": summary["evidence_label"],
            }
        ],
        use_container_width=True,
        hide_index=True,
    )

dev_path = paths.development_report
if not dev_path.exists():
    st.info(f"No development experiment report found at {dev_path}.")
else:
    report = json.loads(dev_path.read_text(encoding="utf-8"))
    st.markdown("**Development model × window ranking**")
    st.dataframe(
        [
            {
                "Model": row["model"],
                "Window": row["window"],
                "Log Loss": round(row["log_loss"], 5),
                "Brier": round(row["brier"], 5),
                "ECE": round(row["ece"], 5),
                "ROC-AUC": round(row["roc_auc"], 4),
                "N": row["n_test"],
                "Evidence": row["evidence_label"],
            }
            for row in load_model_window_ranking(report)
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.markdown("**Development calibration / reliability comparison**")
    st.dataframe(
        [
            {
                "Method": row["method"],
                "Log Loss": round(row["log_loss"], 5),
                "Brier": round(row["brier"], 5),
                "ECE": round(row["ece"], 5),
                "N": row["n_test"],
                "Evidence": row["evidence_label"],
            }
            for row in load_calibration_comparison(report)
        ],
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader(PROSPECTIVE_EVIDENCE_LABEL)
st.caption(
    "Live production predictions joined to finished games in the journal. "
    "This is monitoring only — not holdout evidence and not used for retraining."
)

predictions = read_jsonl(paths.predictions)
journal = read_jsonl(paths.journal)
prospective = build_prospective_model_quality(predictions, journal)
st.write(prospective["note"])
meta_cols = st.columns(4)
meta_cols[0].metric("Resolved N", prospective["resolved_count"])
meta_cols[1].metric("Pending N", prospective["pending_count"])
meta_cols[2].write(
    f"Evaluation start: {prospective['evaluation_start_date'] or '—'}"
)
meta_cols[3].write(
    f"Model version(s): {', '.join(prospective['model_versions']) or '—'}"
)
_render_metric_block(
    "Prospective probability metrics",
    prospective["metrics"],
    sample_label=f"{PROSPECTIVE_EVIDENCE_LABEL} · resolved predictions only",
)

if prospective["probability_buckets"]:
    st.markdown("**Prospective reliability by probability bucket (P(home))**")
    st.dataframe(prospective["probability_buckets"], use_container_width=True, hide_index=True)
