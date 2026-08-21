"""Betting results dashboard — PLAY/PASS selection outcomes only."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.dashboard_analytics import (
    BETTING_RESULTS_NOTE,
    DashboardPaths,
    build_betting_results_summary,
    read_jsonl,
)

st.set_page_config(page_title="Betting Results", layout="wide")
st.title("Betting Results")
st.caption("BETTING STRATEGY decides whether disagreement is actionable (PLAY/PASS).")
st.warning(BETTING_RESULTS_NOTE)

paths = DashboardPaths(
    predictions=Path(os.environ.get("PREDICTIONS_STORE_PATH", DashboardPaths.predictions)),
    journal=Path(os.environ.get("PREDICTION_JOURNAL_PATH", DashboardPaths.journal)),
)

if not paths.predictions.exists():
    st.warning(f"No predictions found at {paths.predictions}.")
    st.stop()

predictions = read_jsonl(paths.predictions)
journal = read_jsonl(paths.journal)
summary = build_betting_results_summary(predictions, journal)

cols = st.columns(4)
cols[0].metric("PLAY count", summary["play_count"])
cols[1].metric("Wins", summary["wins"])
cols[2].metric("Losses", summary["losses"])
cols[3].metric("Pending", summary["pending"])

result_cols = st.columns(4)
result_cols[0].metric(
    "Win rate",
    "—" if summary["win_rate"] is None else f"{summary['win_rate']:.1%}",
)
result_cols[1].metric(
    "ROI (flat 1u)",
    "—" if summary["roi"] is None else f"{summary['roi']:.1%}",
)
result_cols[2].metric(
    "Units",
    "—" if summary["units"] is None else round(summary["units"], 2),
)
result_cols[3].metric(
    "Average |edge| on plays",
    "—" if summary["average_edge"] is None else round(summary["average_edge"], 4),
)

st.caption(
    f"Flat 1-unit stake on PLAY rows with stored American odds "
    f"(staked={summary['staked_units']}, missing odds={summary['missing_odds_count']}). "
    "PASS rows are excluded."
)
if summary["average_odds"] is not None:
    st.caption(f"Average American odds on the picked side: {summary['average_odds']:.0f}")

st.subheader("Edge bucket performance (PLAY rows only)")
st.dataframe(summary["edge_bucket_performance"], use_container_width=True, hide_index=True)
