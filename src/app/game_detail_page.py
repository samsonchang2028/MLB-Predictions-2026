"""APP-005 - Streamlit game detail page.

Thin display layer only, same convention as APP-001's daily board: all
figures are read verbatim via :func:`app.game_detail.load_game_detail`. Shows
the PIPE-001 prediction plus the PIPE-004 detail artifacts (feature
breakdown, multi-book odds comparison) for one game, reached by clicking a
row on the daily board or by loading directly with
``?game_pk=...&run_date=...``.

Normally reached by clicking a row on the daily board, which requires
running via the multipage root entrypoint:

    streamlit run streamlit_app.py

Store/artifact paths default alongside ``state/predictions/``; override with
the same ``PREDICTIONS_STORE_PATH`` env var as the daily board, plus
``GAME_FEATURES_STORE_PATH`` / ``ODDS_BOOKS_STORE_PATH``.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from app.game_detail import load_game_detail
from pipelines.daily import JsonLinesPredictionStore

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_FEATURES_PATH = Path("state/predictions/game_features.jsonl")
DEFAULT_ODDS_BOOKS_PATH = Path("state/predictions/odds_books.jsonl")

FEATURE_COMPONENT_LABELS = (
    ("starter", "Starting pitcher"),
    ("bullpen", "Bullpen"),
    ("team", "Team"),
)


def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


def _features_path() -> Path:
    return Path(os.environ.get("GAME_FEATURES_STORE_PATH", DEFAULT_FEATURES_PATH))


def _odds_books_path() -> Path:
    return Path(os.environ.get("ODDS_BOOKS_STORE_PATH", DEFAULT_ODDS_BOOKS_PATH))


def _format_american(value: int | None) -> str:
    return f"{value:+d}" if isinstance(value, int) else "n/a"


st.set_page_config(page_title="MLB Game Detail", layout="wide")
st.title("MLB Game Detail")

# session_state carries the selection across the daily board's st.switch_page
# click-through (query params set just before switch_page do not reliably
# survive the navigation); query params remain supported for a direct or
# bookmarked ?game_pk=...&run_date=... URL.
game_pk = st.session_state.get("selected_game_pk") or st.query_params.get("game_pk")
run_date = st.session_state.get("selected_run_date") or st.query_params.get("run_date")

if not game_pk or not run_date:
    st.info(
        "Open this page from a game on the Daily Predictions board, or add "
        "?game_pk=...&run_date=... to the URL."
    )
else:
    path = _store_path()
    if not path.exists():
        st.info(f"No predictions found at {path}. Run the daily pipeline first.")
    else:
        store = JsonLinesPredictionStore(path)
        detail = load_game_detail(
            game_pk,
            run_date,
            predictions_store=store,
            features_path=_features_path(),
            odds_books_path=_odds_books_path(),
        )
        if detail is None:
            st.info(f"No prediction found for game_pk={game_pk} on {run_date}.")
        else:
            st.subheader(detail["matchup"])
            st.caption(f"First pitch (Pacific): {detail['game_start_pacific']}")

            col1, col2, col3 = st.columns(3)
            col1.metric("Model P(home)", f"{detail['model_probability']:.1%}")
            col2.metric("Market P(home) (no-vig)", f"{detail['market_probability']:.1%}")
            col3.metric("Edge", f"{detail['edge']:+.1%}")
            st.caption(
                f"Model version: {detail['model_version']} | "
                f"Canonical source: {detail['canonical_source']} | "
                f"Canonical price: home {_format_american(detail['canonical_home_american'])} "
                f"/ away {_format_american(detail['canonical_away_american'])}"
            )

            st.divider()
            st.subheader("Why the model said what it said")
            st.caption(
                "Point-in-time starter/bullpen/team features FEAT-004 built for "
                "this prediction, grouped by component. Raw values, not "
                "per-feature model attribution -- no SHAP/feature-importance "
                "explainability exists in this codebase yet."
            )
            features = detail["features"]
            if not features:
                st.info(
                    "No feature breakdown captured for this prediction "
                    "(predictions made before PIPE-004 shipped don't have one)."
                )
            else:
                for component, label in FEATURE_COMPONENT_LABELS:
                    values = features.get(component) or {}
                    if not values:
                        continue
                    st.markdown(f"**{label}**")
                    st.dataframe(
                        [
                            {"Feature": key, "Value": value}
                            for key, value in sorted(values.items())
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

            st.divider()
            st.subheader("Odds by bookmaker")
            st.caption(
                "Comparison only -- the model's edge above is always computed "
                "against the canonical bookmaker, not these."
            )
            if not detail["odds_books"]:
                st.info("No multi-book odds captured for this prediction.")
            else:
                st.dataframe(
                    [
                        {
                            "Bookmaker": book["bookmaker"],
                            "Home Price": book["home_american"],
                            "Away Price": book["away_american"],
                            "Implied P(home)": round(book["implied_home_probability"], 4),
                            "Model vs Book": round(book["model_vs_book_delta"], 4),
                            "Snapshot (Pacific)": book["snapshot_pacific"],
                        }
                        for book in detail["odds_books"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
