"""Streamlit Community Cloud entrypoint for the MLB moneyline dashboards."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="MLB Moneyline Predictor", layout="wide")

st.title("MLB Moneyline Predictor")
st.caption("Deployment entrypoint for the V1 Streamlit dashboards.")

st.markdown(
    """
Use the sidebar to open:

- **Daily Predictions** — reads immutable prediction records from
  `state/predictions/daily.jsonl`.
- **Model Performance** — reads committed experiment reports and the optional
  enriched prediction journal.

The deployed app is intentionally artifact-backed. The local DuckDB database
(`data/mlb.duckdb`) and raw odds archive are ignored local build inputs, so a
Community Cloud deployment only shows data that is committed or otherwise made
available to the app as JSON/report artifacts. Scheduled operator automation can
publish fresh artifacts later.
"""
)
