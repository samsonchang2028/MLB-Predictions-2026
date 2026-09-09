"""Pure Closing Line Value (CLV) helpers for MARKET-008.

Selected-side CLV compares the no-vig market probability on the edge-selected
side at prediction time against the closing snapshot on the same side:

    clv = closing_market_p_selected - prediction_market_p_selected

No I/O; callers supply parsed timestamps and probabilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from market.play_policy import MARKET_NEUTRAL_BAND, edge_selected_side, is_crossover, model_market_agree

CLV_INSUFFICIENT_THRESHOLD = 30

EDGE_BUCKET_SPECS_PP: tuple[tuple[str, float, float | None], ...] = (
    ("0-2pp", 0.0, 0.02),
    ("2-4pp", 0.02, 0.04),
    ("4-6pp", 0.04, 0.06),
    ("6-8pp", 0.06, 0.08),
    ("8pp+", 0.08, None),
)

LEAD_TIME_BUCKET_SPECS_HOURS: tuple[tuple[str, float, float | None], ...] = (
    ("lt_2h", 0.0, 2.0),
    ("2_6h", 2.0, 6.0),
    ("6_24h", 6.0, 24.0),
    ("24h_plus", 24.0, None),
)

STRICT_CLOSING_DEFINITION = (
    "Latest odds_books.jsonl no-vig snapshot for the prediction source bookmaker "
    "on (game_pk, run_date) where prediction_timestamp < snapshot_timestamp "
    "<= game_start_timestamp."
)

PREGAME_LATEST_CLOSING_DEFINITION = (
    "Latest snapshot for source book where snapshot_timestamp <= game_start_timestamp "
    "(may equal prediction-time line -> CLV = 0)."
)


def selected_side_market_probability(row: Mapping[str, Any], *, side: str) -> float | None:
    """Return no-vig P(selected side wins); HOME uses home market P."""
    market_home = row.get("market_probability")
    if not isinstance(market_home, (int, float)) or isinstance(market_home, bool):
        return None
    p_home = float(market_home)
    return p_home if side == "HOME" else 1.0 - p_home


def compute_clv(*, prediction_market_p_selected: float, closing_market_p_selected: float) -> float:
    """Selected-side closing line value in probability points."""
    return closing_market_p_selected - prediction_market_p_selected


def edge_bucket_label(abs_edge_pp: float, specs: Sequence[tuple[str, float, float | None]] | None = None) -> str:
    """Map absolute edge (probability points) to a coarse bucket label."""
    bucket_specs = specs if specs is not None else EDGE_BUCKET_SPECS_PP
    for label, lower, upper in bucket_specs:
        if upper is None and abs_edge_pp >= lower:
            return label
        if upper is not None and lower <= abs_edge_pp < upper:
            return label
    return "unknown"


def lead_time_bucket_label(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    for label, lower, upper in LEAD_TIME_BUCKET_SPECS_HOURS:
        if upper is None and hours >= lower:
            return label
        if upper is not None and lower <= hours < upper:
            return label
    return "unknown"


def market_favorite_role(row: Mapping[str, Any]) -> str:
    """Classify selected side as market favorite / underdog / near_even."""
    side = edge_selected_side(row)
    if side is None:
        return "unknown"
    p_selected = selected_side_market_probability(row, side=side)
    if p_selected is None:
        return "unknown"
    if abs(p_selected - 0.5) <= MARKET_NEUTRAL_BAND:
        return "near_even"
    return "market_favorite" if p_selected > 0.5 else "market_underdog"


def strict_close_valid(
    prediction_ts: datetime | None,
    close_ts: datetime | None,
    start_ts: datetime | None,
) -> bool:
    """True when pred < close <= start (strict CLV window)."""
    if prediction_ts is None or close_ts is None or start_ts is None:
        return False
    return prediction_ts < close_ts <= start_ts


def close_after_first_pitch(
    close_ts: datetime | None,
    start_ts: datetime | None,
) -> bool:
    if close_ts is None or start_ts is None:
        return False
    return close_ts > start_ts


def close_at_or_before_prediction(
    prediction_ts: datetime | None,
    close_ts: datetime | None,
) -> bool:
    if prediction_ts is None or close_ts is None:
        return False
    return close_ts <= prediction_ts


def pregame_latest_valid(close_ts: datetime | None, start_ts: datetime | None) -> bool:
    """Secondary population: any snapshot at or before first pitch."""
    if close_ts is None or start_ts is None:
        return False
    return close_ts <= start_ts


def interpret_clv_verdict(
    *,
    mean_clv: float | None,
    roi: float | None,
    clv_n: int,
    min_clv_n: int = CLV_INSUFFICIENT_THRESHOLD,
) -> dict[str, Any]:
    """Map aggregate CLV + outcome ROI to Cases 1-4."""
    if clv_n < min_clv_n:
        return {
            "case": 4,
            "verdict": "CLV DATA INSUFFICIENT",
            "adr_007_recommendation": (
                "Do not accept ADR-007 or change production PLAY threshold. "
                "File prospective closing-odds capture (MARKET-009) before CLV "
                "can gate PLAY promotion."
            ),
            "rationale": (
                f"Strict closing-line CLV sample size {clv_n} is below the "
                f"{min_clv_n}-game minimum required for evaluation."
            ),
        }

    clv_positive = mean_clv is not None and mean_clv > 0.0
    roi_positive = roi is not None and roi > 0.0

    if clv_positive and roi_positive:
        return {
            "case": 1,
            "verdict": "POSITIVE CLV WITH POSITIVE ROI",
            "adr_007_recommendation": (
                "Continue ADR-007 prospective shadow validation; do not promote "
                "production PLAY or accept ADR-007 yet."
            ),
            "rationale": "Edge-selected side shows favorable close movement and positive flat ROI.",
        }
    if clv_positive and not roi_positive:
        return {
            "case": 2,
            "verdict": "POSITIVE CLV WITH NEGATIVE ROI",
            "adr_007_recommendation": (
                "Edge may contain information but PLAY policy is not validated; "
                "do not accept ADR-007 or change production PLAY threshold."
            ),
            "rationale": "Closing line moved favorably but realized ROI remains negative.",
        }
    return {
        "case": 3,
        "verdict": "NEGATIVE OR FLAT CLV",
        "adr_007_recommendation": (
            "Edge is not validated against the closing market; do not accept "
            "ADR-007 or change production PLAY threshold."
        ),
        "rationale": "Mean CLV is zero or negative on the strict-close population.",
    }


def row_clv_diagnostics(row: Mapping[str, Any]) -> dict[str, Any]:
    """Attach crossover/agreement labels used by slice tables."""
    return {
        "edge_selected_side": edge_selected_side(row),
        "crossover": is_crossover(row),
        "model_market_agree": model_market_agree(row),
        "market_favorite_role": market_favorite_role(row),
    }
