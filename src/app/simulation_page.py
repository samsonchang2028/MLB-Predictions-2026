"""APP-010 - Streamlit simulation comparison dashboard.

Thin display layer only: joins PIPE-001 daily predictions with PIPE-007
simulation artifacts via :func:`app.simulation_board.load_simulation_board`.

Run via the multipage root entrypoint:

    streamlit run streamlit_app.py

then open **Simulation** from the sidebar.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from app.board import available_run_dates, latest_run_date
from app.simulation_board import (
    DEFAULT_SIMULATION_PATH,
    DISAGREEMENT_THRESHOLD,
    JsonLinesSimulationStore,
    available_simulation_run_dates,
    latest_simulation_run_date,
    load_simulation_board_with_diagnostics,
    slate_probability_chart_frame,
    total_runs_distribution_frame,
)
from pipelines.daily import JsonLinesPredictionStore

DEFAULT_DAILY_PATH = Path("state/predictions/daily.jsonl")


def _format_prob(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1%}"


def _format_runs(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _daily_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_DAILY_PATH))


def _simulation_path() -> Path:
    return Path(os.environ.get("SIMULATION_STORE_PATH", DEFAULT_SIMULATION_PATH))


st.set_page_config(page_title="MLB Simulation Comparison", layout="wide")
st.title("Simulation Comparison")
st.caption(
    "XGBoost is the ADR-006 moneyline lock. Simulation is research/V2 and drives "
    "the totals/runs view. Times are displayed in Pacific time "
    "(America/Los_Angeles)."
)

simulation_path = _simulation_path()
if not simulation_path.exists():
    st.info(
        f"No simulation artifacts found at {simulation_path}. Run the daily "
        "operator with simulation enabled (PIPE-007) to populate "
        "`simulation.jsonl`."
    )
    st.stop()

daily_path = _daily_path()
if not daily_path.exists():
    st.info(f"No daily predictions found at {daily_path}. Run the daily pipeline first.")
    st.stop()

daily_store = JsonLinesPredictionStore(daily_path)
simulation_store = JsonLinesSimulationStore(simulation_path)

daily_dates = available_run_dates(daily_store)
simulation_dates = available_simulation_run_dates(simulation_store)
dates = sorted(set(daily_dates) | set(simulation_dates))
if not dates:
    st.info("No slate dates found in daily or simulation artifacts.")
    st.stop()

default_date = latest_run_date(daily_store) or latest_simulation_run_date(simulation_store)
selected_date = st.sidebar.selectbox(
    "Slate date",
    options=dates,
    index=dates.index(default_date) if default_date in dates else len(dates) - 1,
    help="Filters by operator run_date / MLB official slate date.",
)

report = load_simulation_board_with_diagnostics(
    daily_store,
    simulation_store,
    run_date=selected_date,
)
rows = report["rows"]
skipped = report["skipped"]

if skipped:
    st.warning(
        f"Skipped {len(skipped)} malformed simulation record(s) for slate date "
        f"{selected_date}. Valid records are still shown."
    )
    with st.expander("Skipped malformed simulation records"):
        st.dataframe(skipped, use_container_width=True, hide_index=True)

if not rows:
    st.info(f"No joined daily/simulation rows found for slate date {selected_date}.")
    st.stop()

st.subheader("Slate comparison")
st.caption(
    f"Disagreement flag when |XGBoost - Simulation| > "
    f"{DISAGREEMENT_THRESHOLD:.0%} on home-win probability."
)
st.dataframe(
    [
        {
            "First Pitch (Pacific)": row["game_start_pacific"],
            "Matchup": row["matchup"],
            "P(home) XGB": None if row["p_home_xgb"] is None else round(row["p_home_xgb"], 4),
            "P(home) Sim": None if row["p_home_sim"] is None else round(row["p_home_sim"], 4),
            "P(home) Market": None
            if row["p_home_market"] is None
            else round(row["p_home_market"], 4),
            "Sim Projected Total Runs": row["total_runs_mean"],
            "Disagreement": "Yes" if row["disagreement"] else "",
        }
        for row in rows
    ],
    use_container_width=True,
    hide_index=True,
)

chart_frame = slate_probability_chart_frame(rows)
if chart_frame:
    st.subheader("P(home) by source")
    chart_df = pd.DataFrame.from_dict(chart_frame, orient="index")
    st.bar_chart(chart_df)

st.subheader("Per-game simulation detail")
for row in rows:
    title = row["matchup"]
    if row["disagreement"]:
        title = f"{title} — XGB/Sim disagree"
    with st.expander(title):
        metric_cols = st.columns(4)
        metric_cols[0].metric("P(home) XGB", _format_prob(row["p_home_xgb"]))
        metric_cols[1].metric("P(home) Sim", _format_prob(row["p_home_sim"]))
        metric_cols[2].metric("P(home) Market", _format_prob(row["p_home_market"]))
        metric_cols[3].metric("Projected total runs", _format_runs(row["total_runs_mean"]))

        detail_cols = st.columns(3)
        detail_cols[0].write(
            {
                "home_runs_mean": row["home_runs_mean"],
                "away_runs_mean": row["away_runs_mean"],
                "total_runs_median": row["total_runs_median"],
                "n_trials": row["n_trials"],
            }
        )
        detail_cols[1].write(
            {
                "totals_line": row["totals_line"],
                "p_over": row["p_over"],
                "p_under": row["p_under"],
            }
        )
        detail_cols[2].write(
            {
                "simulation_model_version": row["simulation_model_version"],
                "simulation_build_id": row["simulation_build_id"],
                "simulation_timestamp_pacific": row["simulation_timestamp_pacific"],
                "prediction_timestamp_pacific": row["prediction_timestamp_pacific"],
                "xgb_model_version": row["xgb_model_version"],
            }
        )

        distribution = total_runs_distribution_frame(row)
        if distribution is not None:
            dist_df = pd.DataFrame(
                distribution["values"],
                index=distribution["index"],
            )
            if distribution["kind"] == "histogram":
                st.caption("Total runs distribution (simulation trial histogram)")
                st.bar_chart(dist_df)
            else:
                st.caption("Total runs distribution (stored simulation quantiles)")
                st.line_chart(dist_df)
        elif row["total_runs_mean"] is not None:
            st.caption(
                "Mean/median totals are shown above. Histogram or quantile bins were "
                "not stored in the simulation artifact for this game."
            )
