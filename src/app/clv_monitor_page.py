"""APP-016 — Streamlit CLV monitor page."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.clv_monitor import CLV_DISCLAIMER, build_clv_dashboard
from market.odds_closes import prediction_anchor_label

DEFAULT_PREDICTIONS = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL = Path("state/predictions/journal.jsonl")
DEFAULT_ODDS_BOOKS = Path("state/predictions/odds_books.jsonl")
DEFAULT_ODDS_CLOSES = Path("state/predictions/odds_closes.jsonl")


def _path(env_key: str, default: Path) -> Path:
    return Path(os.environ.get(env_key, default))


@st.cache_data(ttl=300, show_spinner="Refreshing CLV metrics…")
def _load_dashboard(
    predictions: str,
    journal: str,
    odds_books: str,
    odds_closes: str | None,
    anchor: str,
) -> dict:
    return build_clv_dashboard(
        predictions_path=Path(predictions),
        journal_path=Path(journal),
        odds_books_path=Path(odds_books),
        odds_closes_path=Path(odds_closes) if odds_closes else None,
        prediction_anchor=anchor,  # type: ignore[arg-type]
        run_id="streamlit-live",
    )


st.set_page_config(page_title="CLV Monitor", layout="wide")
st.title("Closing Line Value (CLV) Monitor")
st.warning(CLV_DISCLAIMER)

with st.expander("How to read CLV on this page", expanded=False):
    st.markdown(
        """
**CLV** measures whether the closing line moved toward the side you picked:

`CLV = closing_market_prob(selected side) − prediction_market_prob(selected side)`

| Reading | Meaning |
|---------|---------|
| **Positive CLV** | Market moved in your favor before first pitch |
| **Zero / negative CLV** | Market did not move your way |
| **Strict CLV N** | Games with a real close in `odds_closes.jsonl` (`pred < close ≤ first pitch`) |
| **Naive independence** | Not shown here — see Parlay Builder for parlay math |

**Prediction anchor**
- **Latest** *(default)* — board default; use for live prospective monitoring.
- **Earliest** — retrospective backfill population; higher N but not the live board default.

**Capture health** — live rows come from the 10-minute closing-odds timer; backfill rows
are historical. Strict CLV needs **live** rows to grow over time.

This page refreshes every **5 minutes** automatically, or click **Refresh now** in the sidebar.
        """
    )

predictions_path = _path("PREDICTIONS_STORE_PATH", DEFAULT_PREDICTIONS)
journal_path = _path("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL)
odds_books_path = _path("ODDS_BOOKS_STORE_PATH", DEFAULT_ODDS_BOOKS)
odds_closes_path = _path("ODDS_CLOSES_PATH", DEFAULT_ODDS_CLOSES)

if st.sidebar.button("Refresh now"):
    _load_dashboard.clear()

anchor_label = st.sidebar.radio(
    "Prediction anchor",
    options=("Latest (prospective)", "Earliest (retrospective)"),
    index=0,
)
anchor = "latest" if anchor_label.startswith("Latest") else "earliest"

if not predictions_path.exists():
    st.info(f"No predictions found at {predictions_path}. Run the daily operator first.")
    st.stop()

odds_closes_str = str(odds_closes_path) if odds_closes_path.exists() else None
if odds_closes_str is None:
    st.info(
        f"No {odds_closes_path} yet — strict CLV will be limited. "
        "Install closing-odds capture on the homelab."
    )

dashboard = _load_dashboard(
    str(predictions_path),
    str(journal_path),
    str(odds_books_path),
    odds_closes_str,
    anchor,
)

capture = dashboard.get("capture_summary") or {}
st.subheader("Capture health")
cap_cols = st.columns(4)
cap_cols[0].metric("odds_closes rows", capture.get("total_rows", 0))
cap_cols[1].metric("Live captures", capture.get("live_pregame_rows", 0))
cap_cols[2].metric("Backfill rows", capture.get("backfill_rows", 0))
cap_cols[3].metric(
    "Latest close snapshot",
    capture.get("latest_snapshot_timestamp") or "—",
)

if dashboard["status"] == "integrity_error":
    st.error(f"CLV integrity check failed: {dashboard.get('error')}")
    st.stop()

if dashboard["status"] == "no_predictions":
    st.info("No predictions available for CLV evaluation.")
    st.stop()

st.caption(
    f"Anchor: {prediction_anchor_label(anchor)} · "
    f"Generated: {dashboard.get('generated_at', '—')}"
)

metrics = dashboard["metrics"]
st.subheader("Strict CLV (primary)")
m_cols = st.columns(6)
m_cols[0].metric(
    "Strict N",
    metrics["strict_clv_n"],
    help=f"Minimum {dashboard['min_clv_n']} games required for a gate decision.",
)
m_cols[1].metric(
    "Mean CLV",
    "—"
    if metrics["strict_mean_clv_pp"] is None
    else f"{metrics['strict_mean_clv_pp'] * 100:.2f} pp",
)
m_cols[2].metric(
    "Median CLV",
    "—"
    if metrics["strict_median_clv_pp"] is None
    else f"{metrics['strict_median_clv_pp'] * 100:.2f} pp",
)
m_cols[3].metric(
    "Positive CLV rate",
    "—"
    if metrics["strict_positive_clv_rate"] is None
    else f"{metrics['strict_positive_clv_rate']:.1%}",
)
m_cols[4].metric(
    "Strict ROI",
    "—"
    if metrics["strict_outcome_roi"] is None
    else f"{metrics['strict_outcome_roi']:.1%}",
)
m_cols[5].metric("Verdict", dashboard.get("verdict") or "—")

if dashboard.get("verdict_rationale"):
    st.caption(dashboard["verdict_rationale"])
if dashboard.get("adr_007_recommendation"):
    st.caption(f"ADR-007: {dashboard['adr_007_recommendation']}")

st.subheader("Secondary context")
s_cols = st.columns(3)
s_cols[0].metric("Pregame-latest CLV N", metrics["pregame_clv_n"])
s_cols[1].metric(
    "Pregame mean CLV",
    "—"
    if metrics["pregame_mean_clv_pp"] is None
    else f"{metrics['pregame_mean_clv_pp'] * 100:.2f} pp",
)
s_cols[2].metric(
    "Full-pop ROI (latest/game)",
    "—"
    if metrics["full_population_roi"] is None
    else f"{metrics['full_population_roi']:.1%}",
)

if dashboard.get("rejection_summary"):
    st.subheader("Strict-close rejections")
    rejection_rows = [
        {"Reason": reason, "Count": count}
        for reason, count in sorted(dashboard["rejection_summary"].items())
    ]
    st.dataframe(rejection_rows, use_container_width=True, hide_index=True)

st.subheader("Recent strict-close games")
if dashboard["strict_clv_rows"]:
    st.dataframe(dashboard["strict_clv_rows"], use_container_width=True, hide_index=True)
else:
    st.info("No strict-close games yet for the selected anchor.")
