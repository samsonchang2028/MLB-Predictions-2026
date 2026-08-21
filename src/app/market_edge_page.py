"""Market edge dashboard — model vs no-vig market disagreement."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.board import available_run_dates, latest_run_date, load_daily_board
from app.dashboard_analytics import (
    DashboardPaths,
    EDGE_PROFITABILITY_NOTE,
    build_market_edge_summary,
    read_jsonl,
    resolved_prediction_rows,
)
from observability.journal import JsonLinesJournalStore
from pipelines.daily import JsonLinesPredictionStore

st.set_page_config(page_title="Market Edge", layout="wide")
st.title("Market Edge")
st.caption(
    "MODEL estimates P(home). MARKET compares that probability to the sportsbook "
    "no-vig price. Edge is disagreement — not proof of profitability."
)
st.info(EDGE_PROFITABILITY_NOTE)

paths = DashboardPaths(
    predictions=Path(os.environ.get("PREDICTIONS_STORE_PATH", DashboardPaths.predictions)),
    journal=Path(os.environ.get("PREDICTION_JOURNAL_PATH", DashboardPaths.journal)),
)

if not paths.predictions.exists():
    st.warning(f"No predictions found at {paths.predictions}.")
    st.stop()

store = JsonLinesPredictionStore(paths.predictions)
dates = available_run_dates(store)
if not dates:
    st.warning("No predictions in the store yet.")
    st.stop()

default_date = latest_run_date(store)
selected_date = st.sidebar.selectbox(
    "Slate date",
    options=dates,
    index=dates.index(default_date) if default_date in dates else len(dates) - 1,
)
journal_store = (
    JsonLinesJournalStore(paths.journal) if paths.journal.exists() else None
)
rows = load_daily_board(store, run_date=selected_date, journal_store=journal_store)
resolved = resolved_prediction_rows(read_jsonl(paths.predictions), read_jsonl(paths.journal))
summary = build_market_edge_summary(rows, resolved_rows=resolved)

metric_cols = st.columns(4)
metric_cols[0].metric("Games on slate", summary["game_count"])
metric_cols[1].metric(
    "Average edge",
    "—" if summary["average_edge"] is None else round(summary["average_edge"], 4),
)
metric_cols[2].metric(
    "Average |edge|",
    "—" if summary["average_abs_edge"] is None else round(summary["average_abs_edge"], 4),
)
metric_cols[3].metric(
    "Edge range",
    "—"
    if summary["edge_min"] is None
    else f"{summary['edge_min']:.3f} to {summary['edge_max']:.3f}",
)

st.subheader("Edge distribution (selected slate)")
st.dataframe(summary["edge_distribution"], use_container_width=True, hide_index=True)

st.subheader("Largest model-market disagreements")
st.dataframe(summary["largest_disagreements"], use_container_width=True, hide_index=True)

st.subheader("Edge bucket performance (resolved production predictions)")
st.caption("Uses journaled results across all slates; not simulated holdout ROI.")
st.dataframe(summary["edge_bucket_performance"], use_container_width=True, hide_index=True)
