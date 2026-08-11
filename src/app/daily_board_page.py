"""APP-001 - Streamlit daily prediction board.

Thin display layer only: no business/model/market logic lives here. All
figures are read verbatim from PIPE-001 prediction records via
:func:`app.board.load_daily_board`, which itself does no probability/edge
math (see that module's docstring for the pass/play threshold caveat).

Run with:

    streamlit run src/app/daily_board_page.py

The prediction store path defaults to ``state/predictions/daily.jsonl``
(same convention as ``state/data-certifications/``); override with the
``PREDICTIONS_STORE_PATH`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.board import DEFAULT_EDGE_THRESHOLD, load_daily_board
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
    rows = load_daily_board(JsonLinesPredictionStore(path))
    if not rows:
        st.info("No predictions in the store yet.")
    else:
        st.caption(
            "Indicator uses a synthetic, display-only edge threshold "
            f"(|edge| >= {DEFAULT_EDGE_THRESHOLD:.0%}); no real staking policy "
            "exists in this codebase yet -- treat PLAY/PASS as a label, not "
            "a recommendation."
        )
        st.dataframe(
            [
                {
                    "Matchup": row["matchup"],
                    "Model P(home)": round(row["model_probability"], 4),
                    "Market P(home)": round(row["market_probability"], 4),
                    "Edge": round(row["edge"], 4),
                    "Odds Snapshot": str(row["odds_snapshot_timestamp"]),
                    "Model Version": row["model_version"],
                    "Indicator": "PLAY" if row["play"] else "PASS",
                }
                for row in rows
            ],
            use_container_width=True,
            hide_index=True,
        )
