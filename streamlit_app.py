"""Streamlit Community Cloud entrypoint for the MLB moneyline dashboards."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd
import streamlit as st

from app.homepage import ArtifactPaths, build_homepage_summary

st.set_page_config(page_title="MLB Moneyline Predictor", layout="wide")

summary = build_homepage_summary(ArtifactPaths())

st.title("MLB Moneyline Predictor")
st.caption("Daily MLB moneyline model dashboard · artifact-backed V1")

if summary["missing_artifacts"]:
    st.info(
        "Some prediction artifacts are not available yet. Run the local daily "
        "operator/result enrichment or publish artifacts before expecting live "
        f"dashboard data. Missing: {', '.join(summary['missing_artifacts'])}"
    )

performance = summary["play_performance_7d"]
slate = summary["slate_snapshot"]

st.subheader("Your plays — last 7 days")
hero_col, trend_col = st.columns([1, 2])
with hero_col:
    if performance["finished"]:
        win_rate_pct = round(performance["win_rate"] * 100, 1)
        st.metric(
            "Play win rate",
            f"{win_rate_pct}%",
            help="Finished plays only; PASS picks are excluded.",
        )
        st.caption(
            f"{performance['wins']} wins · {performance['losses']} losses · "
            f"{performance['pending']} pending"
        )
    else:
        st.metric("Play win rate", "—")
        st.caption("No finished plays in the last seven slates yet.")

with trend_col:
    daily_rows = performance["daily"]
    if daily_rows:
        daily_df = pd.DataFrame(daily_rows)
        st.caption("Daily wins and losses (plays only)")
        st.bar_chart(
            daily_df.set_index("run_date")[["wins", "losses"]],
            color=["#2ecc71", "#e74c3c"],
            stack=False,
        )
        trend_df = daily_df.dropna(subset=["win_rate"])
        if not trend_df.empty:
            st.caption("Daily win rate on finished plays")
            st.line_chart(
                trend_df.set_index("run_date")["win_rate"],
                color="#3498db",
            )
    else:
        st.info("Run the daily operator to start tracking play results.")

st.subheader("Today's slate")
if summary["latest_run_date"]:
    st.caption(f"Latest slate: {summary['latest_run_date']}")
else:
    st.caption("No predictions published yet.")

slate_col, results_col = st.columns(2)
with slate_col:
    slate_df = pd.DataFrame(
        {
            "Plays": [slate["plays"]],
            "Pass": [slate["passes"]],
            "Awaiting data": [slate["awaiting"]],
        },
        index=["Today's slate"],
    )
    st.caption("How today's games are split")
    st.bar_chart(
        slate_df,
        horizontal=True,
        color=["#9b59b6", "#bdc3c7", "#f39c12"],
    )

with results_col:
    if slate["plays"]:
        results_df = pd.DataFrame(
            {
                "Wins": [slate["play_wins"]],
                "Losses": [slate["play_losses"]],
                "Pending": [slate["play_pending"]],
            },
            index=["Play results"],
        )
        st.caption("Today's play results")
        st.bar_chart(
            results_df,
            horizontal=True,
            color=["#2ecc71", "#e74c3c", "#95a5a6"],
        )
    else:
        st.info("No plays flagged on the latest slate.")

st.subheader("Model")
st.write(summary["model_identity"])
if summary["holdout_metrics"]:
    holdout = summary["holdout_metrics"]
    metric_cols = st.columns(3)
    if "log_loss" in holdout:
        metric_cols[0].metric("Holdout log loss", round(holdout["log_loss"], 4))
    if "brier" in holdout:
        metric_cols[1].metric("Holdout Brier", round(holdout["brier"], 4))
    if "accuracy" in holdout:
        metric_cols[2].metric("Holdout accuracy", f"{holdout['accuracy']:.1%}")
    st.caption(summary["methodology_label"])
else:
    st.caption(summary["methodology_label"])

with st.expander("Data freshness"):
    fresh_df = pd.DataFrame(
        {
            "updated": [
                summary["predictions_last_updated"] or "Missing",
                summary["odds_last_updated"] or "Missing",
                summary["results_last_refreshed"] or "Missing",
            ],
        },
        index=["Predictions", "Odds snapshot", "Results"],
    )
    st.dataframe(fresh_df, use_container_width=True)

if summary["skipped_count"]:
    with st.expander("Games waiting on data"):
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
