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

from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    DEFAULT_SKIPPED_PATH,
    available_run_dates,
    latest_run_date,
    load_daily_board_with_diagnostics,
    load_starter_pending_games,
)
from observability.journal import JsonLinesJournalStore
from pipelines.daily import JsonLinesPredictionStore

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")




def _pick_result_label(row: dict) -> str:
    if not row.get("play"):
        return "No Play"
    if row.get("correct") is True:
        return "Correct"
    if row.get("correct") is False:
        return "Wrong"
    return "Pending"

def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


def _skipped_path() -> Path:
    return Path(os.environ.get("SKIPPED_STORE_PATH", DEFAULT_SKIPPED_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


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
        journal_path = _journal_path()
        journal_store = JsonLinesJournalStore(journal_path) if journal_path.exists() else None
        board_report = load_daily_board_with_diagnostics(
            store, run_date=selected_date, journal_store=journal_store
        )
        pending_starters = load_starter_pending_games(_skipped_path(), selected_date)
        pending_game_pks = {row["game_pk"] for row in pending_starters}
        rows = [
            row for row in board_report["rows"] if row["game_pk"] not in pending_game_pks
        ]
        skipped = board_report["skipped"]
        st.caption(
            "Times are displayed in Pacific time (America/Los_Angeles). "
            "Pick means the side the model prefers relative to the market price. "
            "PLAY/PASS still uses a synthetic, "
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
        if pending_starters:
            st.info(
                f"{len(pending_starters)} game(s) are waiting for starting-pitcher "
                "announcements. The model will not predict those slates yet."
            )
            st.dataframe(
                [
                    {
                        "First Pitch (Pacific)": row["game_start_pacific"],
                        "Matchup": row["matchup"],
                        "Status": row["message"],
                    }
                    for row in pending_starters
                ],
                use_container_width=True,
                hide_index=True,
            )
        if not rows:
            st.info(f"No valid predictions found for slate date {selected_date}.")
        else:
            st.caption("Select a row to open its detail page (pitcher/bullpen stats, multi-book odds).")
            st.caption("This is not a staking policy. PASS rows are no-play rows, not wins or losses.")
            selection = st.dataframe(
                [
                    {
                        "Slate Date": row["run_date"],
                        "First Pitch (Pacific)": row["game_start_pacific"],
                        "Matchup": row["matchup"],
                        "Pick": row["pick"],
                        "Recommendation": row["recommendation"],
                        "Result": row["result_label"] or row["result_status"],
                        "Pick Result": _pick_result_label(row),
                        "Model Chance": round(row["model_chance"], 4),
                        "Market Chance": round(row["market_chance"], 4),
                        "Difference": round(row["difference"], 4),
                        "Odds Snapshot (Pacific)": row["odds_snapshot_pacific"],
                        "Prediction Time (Pacific)": row["prediction_timestamp_pacific"],
                        "Raw P(home)": round(row["model_probability"], 4),
                        "Raw Market P(home)": round(row["market_probability"], 4),
                        "Raw Edge": round(row["edge"], 4),
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
