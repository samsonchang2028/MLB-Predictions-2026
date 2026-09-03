"""Streamlit home page — daily model signal dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd
import streamlit as st

from app.homepage import ArtifactPaths
from app.signal_dashboard import build_signal_dashboard, format_edge_pp, format_probability

st.set_page_config(page_title="MLB Moneyline Predictor", layout="wide")

dashboard = build_signal_dashboard(ArtifactPaths())
status = dashboard["system_status"]
signal_summary = dashboard["signal_summary"]
signal_table = dashboard["signal_table"]
uncertainty_candidates = dashboard["uncertainty_candidates"]

st.title("MLB Moneyline Predictor")
st.markdown(
    """
The model estimates **P(home team wins)**. The market engine compares that probability
against live no-vig sportsbook probability. Large differences are **model-market
disagreements**, not guaranteed outcomes.
"""
)

if dashboard["freshness_warnings"]:
    st.warning(" · ".join(dashboard["freshness_warnings"]))

st.subheader("System status")
status_cols = st.columns(6)
status_cols[0].metric("Today's games", status["games_today"])
status_cols[1].metric("Predictions generated", status["predictions_generated"])
status_cols[2].metric("Games with odds", status["games_with_odds"])
status_cols[3].metric("Odds freshness", dashboard["odds_last_updated"] or "Missing")
status_cols[4].metric("Model version", status["model_version"])
status_cols[5].metric("Data quality", status["data_quality"])
st.caption(
    f"Build ID: {status['build_id']} · Latest prediction update: "
    f"{dashboard['predictions_last_updated'] or 'Missing'}"
)

st.subheader("Today's signal summary")
summary_cols = st.columns(4)
summary_cols[0].metric("Games", signal_summary["games"])
summary_cols[1].metric("With odds", signal_summary["with_odds"])
summary_cols[2].metric("Signals above threshold", signal_summary["signals_above_threshold"])
summary_cols[3].metric("Avg |edge|", signal_summary["average_abs_edge_pp"])
edge_cols = st.columns(3)
edge_cols[0].metric("Largest home edge", signal_summary["largest_home_edge_pp"])
edge_cols[1].metric("Largest away edge", signal_summary["largest_away_edge_pp"])
edge_cols[2].metric("Missing odds", signal_summary["missing_odds"])

st.subheader("Today's best signals")
if not signal_table:
    st.info("No predictions for the latest slate yet. Run the daily operator first.")
else:
    display_rows = [
        {
            "Matchup": row["matchup"],
            "First pitch": row["first_pitch"],
            "Value side": row["model_side"],
            "Model P (side)": format_probability(row["model_side_probability"]),
            "Market P (side)": format_probability(row["market_side_probability"]),
            "Edge": row["edge_pp_display"],
            "Odds": row["sportsbook_odds"],
            "Odds snapshot": row["odds_snapshot"],
            "Signal": row["signal_label"],
            "Risk flags": ", ".join(row["risk_flags"]) if row["risk_flags"] else "—",
            "Why": row["why_summary"],
            "EV ($1)": round(row["expected_value"], 3)
            if row["expected_value"] is not None
            else "—",
        }
        for row in signal_table
    ]
    selection = st.dataframe(
        display_rows,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_rows = selection.selection.rows if selection is not None else []
    if selected_rows:
        selected = signal_table[selected_rows[0]]
        from app.signal_dashboard import prepare_selected_game_detail

        detail = prepare_selected_game_detail(
            dashboard["board_rows_by_game"][selected["game_pk"]],
            raw_record=dashboard["raw_by_game"].get(selected["game_pk"]),
            features_path=Path("state/predictions/game_features.jsonl"),
            pending_starter_game_pks=dashboard["pending_starter_pks"],
        )
        st.subheader("Selected game detail")
        st.write(detail["interpretation"])
        detail_cols = st.columns(4)
        detail_cols[0].metric("Model P (side)", format_probability(detail["model_side_probability"]))
        detail_cols[1].metric("Market P (side)", format_probability(detail["market_side_probability"]))
        detail_cols[2].metric("Edge", detail["edge_pp_display"])
        detail_cols[3].metric(
            "EV ($1)",
            "—" if detail["expected_value"] is None else round(detail["expected_value"], 3),
        )
        st.write(
            {
                "Matchup": detail["matchup"],
                "First pitch": detail["first_pitch"],
                "Prediction time": detail["prediction_timestamp"],
                "Odds snapshot": detail["odds_snapshot"],
                "Sportsbook/source": detail["sportsbook_source"],
                "Home odds": detail["home_odds"],
                "Away odds": detail["away_odds"],
                "Model P(home)": format_probability(detail["model_probability_home"]),
                "Market P(home)": format_probability(detail["market_probability_home"]),
                "Raw edge (home)": format_edge_pp(detail["raw_edge"]),
                "Model version": detail["model_version"],
                "Build ID": detail["build_id"],
                "Signal label": detail["signal_label"],
                "Risk flags": detail["risk_flags"],
            }
        )
        st.markdown("**Why this signal?**")
        st.caption(detail["feature_context_note"])
        if detail["feature_groups"]:
            st.dataframe(detail["feature_groups"], use_container_width=True, hide_index=True)
        else:
            st.info("Feature context not available for this game yet.")

st.subheader("Uncertainty-Adjusted Signals")
st.caption(
    "Shadow view only. Expected wins per 100 similar games come from resolved "
    "raw-favorite bucket win rates. The live table uses current resolved history; "
    "shadow comparison uses expanding as-of resolved history (only games whose "
    "first pitch is strictly before the scored game's prediction time). "
    "The uncertainty-adjusted range is a Wilson interval on those "
    "favorite outcomes, not a current-sample CI around today's model P. "
    "Pending games are excluded from bucket estimates."
)
if not uncertainty_candidates:
    st.info("No confidence-adjusted candidates for the latest slate yet.")
else:
    st.dataframe(
        [
            {
                "Matchup": row["matchup"],
                "First pitch": row["first_pitch"],
                "Raw model favorite": row["raw_model_favorite_label"],
                "Model favorite probability": format_probability(row["model_favorite_probability"]),
                "Expected wins per 100 similar games": row["similar_games_wins_per_100"],
                "Uncertainty-adjusted range": row["uncertainty_range_per_100"],
                "Market-implied wins per 100": row["market_wins_per_100"],
                "Conservative edge per 100": row["conservative_edge_per_100"],
                "Offered odds": row["offered_odds"],
                "Lower-bound EV ($1)": round(row["lower_bound_ev"], 3)
                if row["lower_bound_ev"] is not None
                else "—",
                "Baseline label": row["baseline_label"],
                "Challenger label": row["challenger_label"],
                "Risk flags": ", ".join(row["risk_flags"]) if row["risk_flags"] else "—",
                "Bucket N": row["bucket_n"],
            }
            for row in uncertainty_candidates
        ],
        use_container_width=True,
        hide_index=True,
    )

comparison = dashboard.get("shadow_strategy_comparison")
if comparison:
    st.subheader("Shadow strategy comparison (resolved journal)")
    st.caption(comparison["window_label"])
    st.caption(comparison["note"])
    strategy_rows = []
    labels = {
        "baseline_play": "Baseline PLAY",
        "raw_model_favorite": "Raw model favorite",
        "uncertainty_challenger": "Uncertainty-adjusted challenger",
    }
    for key, metrics in comparison["strategies"].items():
        strategy_rows.append(
            {
                "Strategy": labels.get(key, key),
                "N": metrics["n"],
                "Resolved": metrics.get("n_resolved", metrics["wins"] + metrics["losses"]),
                "Wins": metrics["wins"],
                "Losses": metrics["losses"],
                "Pending": metrics["pending"],
                "Win rate": f"{metrics['win_rate']:.1%}" if metrics["win_rate"] is not None else "—",
                "ROI": f"{metrics['roi']:.1%}" if metrics["roi"] is not None else "—",
                "Units": round(metrics["units"], 2) if metrics["units"] is not None else "—",
                "Avg model P": f"{metrics['average_model_probability']:.1%}"
                if metrics["average_model_probability"] is not None
                else "—",
                "Avg market P": f"{metrics['average_market_probability']:.1%}"
                if metrics["average_market_probability"] is not None
                else "—",
                "Avg edge": f"{metrics['average_edge'] * 100:+.1f} pp"
                if metrics["average_edge"] is not None
                else "—",
                "Favorite / underdog": f"{metrics['favorite_count']} / {metrics['underdog_count']}",
                "Home / away": f"{metrics['home_count']} / {metrics['away_count']}",
            }
        )
    st.dataframe(strategy_rows, use_container_width=True, hide_index=True)
    with st.expander("Strategy bucket breakdown"):
        for key, metrics in comparison["strategies"].items():
            label = labels.get(key, key)
            prob_buckets = metrics.get("model_probability_buckets") or []
            edge_buckets = metrics.get("edge_buckets") or []
            if not prob_buckets and not edge_buckets:
                continue
            st.markdown(f"**{label}**")
            if prob_buckets:
                st.caption("Model probability buckets")
                st.dataframe(prob_buckets, use_container_width=True, hide_index=True)
            if edge_buckets:
                st.caption("Edge buckets")
                st.dataframe(edge_buckets, use_container_width=True, hide_index=True)

st.subheader("Today's edge distribution")
if dashboard["edge_buckets"]:
    bucket_df = pd.DataFrame(dashboard["edge_buckets"])
    st.bar_chart(bucket_df.set_index("bucket")["count"])
    st.dataframe(bucket_df, use_container_width=True, hide_index=True)

st.subheader("Model quality snapshot")
st.caption(
    "Model quality evaluates probability accuracy. Finished PLAY results evaluate the "
    "current selection strategy."
)
holdout = dashboard["holdout_metrics"]
if holdout:
    st.markdown("**Historical / Holdout evidence**")
    h_cols = st.columns(4)
    if "log_loss" in holdout:
        h_cols[0].metric("Holdout log loss", round(holdout["log_loss"], 4))
    if "brier" in holdout:
        h_cols[1].metric("Holdout Brier", round(holdout["brier"], 4))
    if "ece" in holdout:
        h_cols[2].metric("Holdout ECE", round(holdout["ece"], 4))
    if "accuracy" in holdout:
        h_cols[3].metric("Holdout accuracy", f"{holdout['accuracy']:.1%}")
    st.caption(dashboard["methodology_label"])
else:
    st.info("Holdout metrics unavailable locally.")

prospective = dashboard["prospective"]["metrics"]
if prospective:
    st.markdown("**Prospective production monitoring**")
    p_cols = st.columns(4)
    p_cols[0].metric("Prospective log loss", round(prospective["log_loss"], 4))
    p_cols[1].metric("Prospective Brier", round(prospective["brier"], 4))
    p_cols[2].metric("Prospective ECE", round(prospective["ece"], 4))
    p_cols[3].metric("Prospective N", prospective["n"])
    st.caption(dashboard["prospective"]["note"])

st.subheader("Finished PLAY results")
finished = dashboard["finished_play_results"]
st.caption(
    f"{finished['window_label']} {finished['note']} "
    "Short-term PLAY win rate is noisy and should not be used alone to judge model quality."
)
f_cols = st.columns(5)
f_cols[0].metric("Finished plays", finished["play_count"])
f_cols[1].metric("Wins", finished["wins"])
f_cols[2].metric("Losses", finished["losses"])
f_cols[3].metric("Pending", finished["pending"])
f_cols[4].metric(
    "Win rate",
    "—" if finished["win_rate"] is None else f"{finished['win_rate']:.1%}",
)
st.caption(
    f"Avg |edge| on plays: {finished['average_edge_pp']} · "
    f"ROI: {'—' if finished['roi'] is None else f'{finished['roi']:.1%}'} · "
    f"Units: {'—' if finished['units'] is None else round(finished['units'], 2)}"
)

st.caption(dashboard["model_identity"])
