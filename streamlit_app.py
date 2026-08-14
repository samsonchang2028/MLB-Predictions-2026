"""Streamlit Community Cloud entrypoint for the MLB moneyline dashboards."""

from __future__ import annotations

import streamlit as st

from app.homepage import ArtifactPaths, build_homepage_summary

st.set_page_config(page_title="MLB Moneyline Predictor", layout="wide")

summary = build_homepage_summary(ArtifactPaths())

st.title("MLB Moneyline Predictor")
st.caption("Daily MLB moneyline model dashboard · artifact-backed V1")

st.markdown(
    """
This dashboard shows the model's current MLB moneyline predictions, whether
finished picks won or lost, and the model's locked V1 performance context. It
does **not** place bets, guarantee outcomes, or update the model from today's
results.
"""
)

if summary["missing_artifacts"]:
    st.info(
        "Some prediction artifacts are not available yet. Run the local daily "
        "operator/result enrichment or publish artifacts before expecting live "
        f"dashboard data. Missing: {', '.join(summary['missing_artifacts'])}"
    )

st.subheader("Today at a glance")
card1, card2, card3, card4 = st.columns(4)
card1.metric("Latest slate", summary["latest_run_date"] or "No predictions")
card2.metric("Games predicted", summary["unique_games_count"])
card3.metric("Displayed plays", summary["plays_count"])
card4.metric("Finished predictions", summary["finished_predictions_count"])

result1, result2, result3, result4 = st.columns(4)
result1.metric("Play wins", summary["play_wins"])
result2.metric("Play losses", summary["play_losses"])
result3.metric("Play pending", summary["play_pending"])
result4.metric("No-play rows", summary["no_play_count"])

st.subheader("Data freshness")
fresh1, fresh2, fresh3 = st.columns(3)
fresh1.metric("Predictions updated", summary["predictions_last_updated"] or "Missing")
fresh2.metric("Odds snapshot", summary["odds_last_updated"] or "Missing")
fresh3.metric("Results refreshed", summary["results_last_refreshed"] or "Missing")

st.subheader("Model")
st.write(summary["model_identity"])
st.caption(summary["methodology_label"])

if summary["holdout_metrics"]:
    st.markdown("Final 2026 holdout evidence:")
    metric_cols = st.columns(len(summary["holdout_metrics"]))
    for column, (name, value) in zip(metric_cols, summary["holdout_metrics"].items()):
        column.metric(name.replace("_", " ").title(), round(value, 4) if isinstance(value, float) else value)

if summary["skipped_count"]:
    st.subheader("Games waiting on data")
    st.write(
        {
            "skipped_count": summary["skipped_count"],
            "reasons": summary["skipped_reasons"],
        }
    )

with st.expander("Artifact sources"):
    st.json(summary["artifact_paths"])

st.markdown(
    """
Use the sidebar for the detailed pages:

- **Daily Predictions** — current slate picks and results.
- **Model Performance** — historical development and final holdout evidence.
- **Game Detail** — per-game feature and odds breakdown.
"""
)
