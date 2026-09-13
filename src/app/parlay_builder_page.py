"""APP-015 — Streamlit parlay builder page (display-only)."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.board import (
    available_run_dates,
    latest_run_date,
    load_daily_board_with_diagnostics,
    load_starter_pending_games,
)
from app.parlay_builder import (
    PARLAY_DISCLAIMER,
    RANK_MODE_LABELS,
    SOURCE_POLICY_LABELS,
    RankMode,
    SourcePolicy,
    build_parlay_report,
)
from app.play_policy_display import enrich_board_rows_with_shadow, latest_raw_records_by_game
from observability.journal import JsonLinesJournalStore
from pipelines.daily import JsonLinesPredictionStore

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")
DEFAULT_SKIPPED_PATH = Path("state/predictions/skipped.jsonl")


def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


def _skipped_path() -> Path:
    return Path(os.environ.get("SKIPPED_STORE_PATH", DEFAULT_SKIPPED_PATH))


def _format_american(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _render_suggestion(suggestion: dict) -> None:
    st.markdown(f"**#{suggestion['rank']} — {suggestion['leg_labels']}**")
    leg_rows = [
        {
            "Pick": leg["pick"],
            "Matchup": leg["matchup"],
            "Model P": f"{leg['model_p']:.1%}",
            "American": _format_american(int(leg["american"])),
            "Implied P": f"{leg['implied_p']:.1%}",
            "Parlay score": round(float(leg["parlay_score"]), 3),
            "Risk flags": leg["risk_flags"],
        }
        for leg in suggestion["legs"]
    ]
    st.dataframe(leg_rows, use_container_width=True, hide_index=True)

    metric_cols = st.columns(3)
    metric_cols[0].metric(
        "Combined decimal odds",
        f"{suggestion['combined_decimal']:.2f}",
    )
    metric_cols[1].metric(
        "Payout on $100",
        f"${suggestion['payout']:.2f}",
    )
    metric_cols[2].metric(
        "Naive independence estimate",
        f"{suggestion['naive_independence_estimate']:.1%}",
        help="Product of leg model probabilities; not a fully modeled joint distribution.",
    )


st.set_page_config(page_title="Parlay Builder", layout="wide")
st.title("Parlay Builder")
st.warning(PARLAY_DISCLAIMER)

with st.expander("How to use this page", expanded=True):
    st.markdown(
        """
This tab **pairs today's strongest single-game picks into example parlays**.
It is for exploration only — not a validated betting system.

### Step 1 — Choose settings (sidebar)

1. **Slate date** — same MLB day as Daily Predictions.
2. **Source policy** — which games become parlay legs:
   - **Raw Model** *(default)* — every game; bet the model's favorite (`P(home) ≥ 50%` → home, else away).
   - **PLAY** — only games that clear the display PLAY rule (`|edge| ≥ 2%`).
   - **Top Edge** — the games with the largest model–market disagreement.
   - **Agreement Only** — model and market favor the same side.
3. **Leg count** — build 2-, 3-, or 4-leg parlays.
4. **Rank by** — how legs are ordered before combinations are built:
   - **Balanced** *(default)* — composite **parlay score** (see below).
   - **Highest model confidence** — highest model probability on the picked side.
   - **Highest expected payout** — longest American prices (bigger underdog payout).

### Step 2 — Read the ranked legs table

Each row is one possible **leg** (one game, one side):

| Column | Meaning |
|--------|---------|
| **Pick** | Side the leg would bet (team name). |
| **Model P** | Model's win probability for that side. |
| **American / Decimal** | DraftKings moneyline at prediction time. |
| **Parlay score** | Higher = stronger leg under **Balanced** ranking. Favors confident picks, model–market agreement, and penalizes crossover / risk flags. **Not the same as edge.** |
| **Agree** | `True` if model and market favor the same side. |
| **Crossover** | `True` if the edge-selected side differs from the raw model favorite — historically a weaker pattern in our data. |
| **Risk flags** | Warnings such as large disagreement or crossover. |

### Step 3 — Read suggested parlays

Open a suggestion to see each leg, then the parlay totals:

| Metric | Meaning |
|--------|---------|
| **Combined decimal odds** | Multiply each leg's decimal odds (e.g. 1.91 × 2.50). |
| **Payout on $100** | What a **$100 stake** returns if **every leg wins** (includes stake). |
| **Naive independence estimate** | Model probability that **all legs win**, assuming games are independent (`P₁ × P₂ × …`). **Not** a full joint model — label it as an estimate only. |

**Implied P** on each leg is the book's no-vig implied probability from that leg's American odds.

### Tips

- Start with **Raw Model + Balanced + 2 legs** if you are unsure.
- If you see **no suggestions**, try another source policy or a smaller leg count — the slate may not have enough eligible games.
- Lower **parlay score** or **Crossover = True** does not forbid a leg, but those legs rank lower for a reason.
        """
    )

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
        )

        policy_label = st.sidebar.radio(
            "Source policy",
            options=list(SOURCE_POLICY_LABELS.values()),
            index=0,
        )
        source_policy = next(
            policy for policy, label in SOURCE_POLICY_LABELS.items() if label == policy_label
        )

        leg_count = st.sidebar.radio("Leg count", options=[2, 3, 4], index=0, horizontal=True)

        rank_label = st.sidebar.radio(
            "Rank by",
            options=list(RANK_MODE_LABELS.values()),
            index=2,
        )
        rank_mode = next(
            mode for mode, label in RANK_MODE_LABELS.items() if label == rank_label
        )

        journal_path = _journal_path()
        journal_store = JsonLinesJournalStore(journal_path) if journal_path.exists() else None
        board_report = load_daily_board_with_diagnostics(
            store,
            run_date=selected_date,
            journal_store=journal_store,
        )
        pending_starters = load_starter_pending_games(_skipped_path(), selected_date)
        pending_game_pks = {row["game_pk"] for row in pending_starters}
        rows = [
            row for row in board_report["rows"] if row["game_pk"] not in pending_game_pks
        ]

        if pending_starters:
            st.info(
                f"{len(pending_starters)} game(s) excluded — waiting for starting-pitcher announcements."
            )

        if not rows:
            st.info(f"No valid predictions found for slate date {selected_date}.")
        else:
            raw_by_game = latest_raw_records_by_game(store.records(), run_date=selected_date)
            shadow_rows = enrich_board_rows_with_shadow(rows, raw_records_by_game=raw_by_game)
            report = build_parlay_report(
                shadow_rows,
                source_policy=source_policy,
                rank_mode=rank_mode,
                leg_count=leg_count,
            )

            st.caption(
                f"Slate {selected_date} · {len(report['eligible_legs'])} eligible legs · "
                f"policy={SOURCE_POLICY_LABELS[source_policy]} · "
                f"rank={RANK_MODE_LABELS[rank_mode]} · {leg_count}-leg parlays"
            )

            if report["status"] == "no_eligible_legs":
                st.info("No eligible legs for the selected source policy.")
            elif report["status"] == "insufficient_legs":
                st.info(
                    f"Only {len(report['ranked_legs'])} eligible leg(s) — need at least "
                    f"{leg_count} for a {leg_count}-leg parlay."
                )

            st.subheader("Ranked parlay legs")
            if report["ranked_legs"]:
                display_legs = [
                    {
                        "Matchup": leg["matchup"],
                        "Pick": leg["pick"],
                        "First pitch": leg["first_pitch"],
                        "Model P": f"{leg['model_p']:.1%}",
                        "American": _format_american(int(leg["american"])),
                        "Decimal": round(float(leg["decimal_odds"]), 3),
                        "Parlay score": round(float(leg["parlay_score"]), 3),
                        "Agree": leg["model_market_agree"],
                        "Crossover": leg["is_crossover"],
                        "Risk flags": leg["risk_flags"],
                    }
                    for leg in report["ranked_legs"]
                ]
                st.dataframe(display_legs, use_container_width=True, hide_index=True)
            else:
                st.info("No ranked legs to show.")

            st.subheader("Suggested parlays")
            if report["suggestions"]:
                for suggestion in report["suggestions"]:
                    with st.expander(
                        f"#{suggestion['rank']} — {suggestion['leg_labels']} "
                        f"(${suggestion['payout']:.0f} on $100)",
                        expanded=suggestion["rank"] == 1,
                    ):
                        _render_suggestion(suggestion)
            elif report["status"] == "ok":
                st.info("No parlay combinations generated for the current settings.")
