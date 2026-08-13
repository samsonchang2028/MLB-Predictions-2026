"""APP-001 - Streamlit daily prediction board.

Thin display layer only: no business/model/market logic lives here. All
figures are read verbatim from PIPE-001 prediction records via
:func:`app.board.load_daily_board`, which itself does no probability/edge
math (see that module's docstring for the pass/play threshold caveat).

Run via the multipage root entrypoint (required for the row click-through to
``pages/3_Game_Detail.py`` to resolve correctly -- ``st.switch_page`` paths
are relative to whatever script ``streamlit run`` was pointed at, not to this
file):

    streamlit run streamlit_app.py

then open "Daily Predictions" from the sidebar. Running this file directly
(``streamlit run src/app/daily_board_page.py``) still renders the board, but
clicking a row will fail to find ``pages/3_Game_Detail.py``.

The prediction store path defaults to ``state/predictions/daily.jsonl``
(same convention as ``state/data-certifications/``); override with the
``PREDICTIONS_STORE_PATH`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.board import DEFAULT_EDGE_THRESHOLD, available_run_dates, latest_run_date, load_daily_board_with_diagnostics
from pipelines.daily import JsonLinesPredictionStore

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")


def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


st.set_page_config(page_title="MLB Daily Predictions", layout="wide")
st.title("MLB Daily Predictions")

path = _store_path()
if not path.exists():
    st.info(f"No predictions found at {path}. Run the daily pipeline first.")
else:
    store = JsonLinesPredictionStore(path)
    dates = available_run_dates(store)
    if not dates:
        st.info("No predictions in the store yet.")
    else:
        default_date = latest_run_date(store)
        selected_date = st.sidebar.selectbox(
            "Slate date",
            options=dates,
            index=dates.index(default_date) if default_date in dates else len(dates) - 1,
            help="Filters by the operator run_date / MLB official slate date.",
        )
        board_report = load_daily_board_with_diagnostics(store, run_date=selected_date)
        rows = board_report["rows"]
        skipped = board_report["skipped"]
        st.caption(
            "Times are displayed in Pacific time (America/Los_Angeles). "
            "Model side is the team whose price the model prefers relative to the "
            "no-vig market probability. PLAY/PASS still uses a synthetic, "
            "display-only edge threshold "
            f"(|edge| >= {DEFAULT_EDGE_THRESHOLD:.0%}); no real staking policy "
            "exists in this codebase yet."
        )
        if skipped:
            st.warning(
                f"Skipped {len(skipped)} malformed prediction record(s) for "
                f"slate date {selected_date}. Valid records are still shown."
            )
            with st.expander("Skipped malformed records"):
                st.dataframe(skipped, use_container_width=True, hide_index=True)
        if not rows:
            st.info(f"No valid predictions found for slate date {selected_date}.")
        else:
            st.caption("Select a row to open its detail page (pitcher/bullpen stats, multi-book odds).")
            selection = st.dataframe(
                [
                    {
                        "Slate Date": row["run_date"],
                        "First Pitch (Pacific)": row["game_start_pacific"],
                        "Matchup": row["matchup"],
                        "Model Side": row["model_side_detail"],
                        "Action Label": row["action_label"],
                        "Model P(home)": round(row["model_probability"], 4),
                        "Market P(home)": round(row["market_probability"], 4),
                        "Edge": round(row["edge"], 4),
                        "Odds Snapshot (Pacific)": row["odds_snapshot_pacific"],
                        "Prediction Time (Pacific)": row["prediction_timestamp_pacific"],
                        "Model Version": row["model_version"],
                    }
                    for row in rows
                ],
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
            )
            selected_rows = selection.selection.rows if selection is not None else []
            if selected_rows:
                selected_game_pk = rows[selected_rows[0]]["game_pk"]
                # st.query_params set right before st.switch_page does not
                # reliably survive the navigation; session_state is the
                # documented way to pass data across a page switch.
                st.session_state["selected_game_pk"] = str(selected_game_pk)
                st.session_state["selected_run_date"] = str(selected_date)
                st.switch_page("pages/3_Game_Detail.py")
