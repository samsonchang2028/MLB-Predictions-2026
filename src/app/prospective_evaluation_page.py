"""Prospective evaluation dashboard — frozen production prediction monitoring."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.dashboard_analytics import (
    PROSPECTIVE_EVIDENCE_LABEL,
    PROSPECTIVE_MONITORING_NOTE,
    DashboardPaths,
    build_prospective_model_quality,
    read_jsonl,
)

st.set_page_config(page_title="Prospective Evaluation", layout="wide")
st.title("Prospective Evaluation")
st.caption(PROSPECTIVE_EVIDENCE_LABEL)
st.info(PROSPECTIVE_MONITORING_NOTE)

paths = DashboardPaths(
    predictions=Path(os.environ.get("PREDICTIONS_STORE_PATH", DashboardPaths.predictions)),
    journal=Path(os.environ.get("PREDICTION_JOURNAL_PATH", DashboardPaths.journal)),
)

if not paths.predictions.exists():
    st.warning(f"No predictions found at {paths.predictions}.")
    st.stop()

predictions = read_jsonl(paths.predictions)
journal = read_jsonl(paths.journal)
summary = build_prospective_model_quality(predictions, journal)
metrics = summary["metrics"]

meta_cols = st.columns(4)
meta_cols[0].write(f"Model/version: {', '.join(summary['model_versions']) or '—'}")
meta_cols[1].write(f"Evaluation start: {summary['evaluation_start_date'] or '—'}")
meta_cols[2].metric("Resolved predictions", summary["resolved_count"])
meta_cols[3].metric("Pending predictions", summary["pending_count"])

if metrics is None:
    st.info("No resolved production predictions yet. Enrich finished games in the journal first.")
    st.stop()

metric_cols = st.columns(5)
metric_cols[0].metric("Log loss", round(metrics["log_loss"], 4))
metric_cols[1].metric("Brier", round(metrics["brier"], 4))
metric_cols[2].metric("Calibration (ECE)", round(metrics["ece"], 4))
metric_cols[3].metric("Accuracy", f"{metrics['accuracy']:.1%}")
metric_cols[4].metric(
    "ROC-AUC",
    "—" if metrics["roc_auc"] is None else round(metrics["roc_auc"], 4),
)
st.caption(
    f"Prospective N={metrics['n']} · home-win base rate={metrics['positive_rate']:.1%}. "
    "Pending games are excluded from all metrics above."
)

st.subheader("Probability buckets (P(home))")
st.dataframe(summary["probability_buckets"], use_container_width=True, hide_index=True)

st.subheader("Reliability view")
st.caption("Average predicted P(home) vs actual home-win rate by bucket.")
st.dataframe(summary["reliability_buckets"], use_container_width=True, hide_index=True)
