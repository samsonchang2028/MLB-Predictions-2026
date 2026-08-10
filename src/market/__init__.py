"""Market probability and edge engine (MARKET-001).

Public API: American-odds conversion, two-way no-vig normalization, edge and
expected value, and provenance/timestamp-guarded model-vs-market evaluation.
See :mod:`market.engine` for formulas and point-in-time rules.
"""

from __future__ import annotations

from market.engine import (
    AMBIGUOUS,
    MATCHED,
    PROBABILITY_SUM_TOLERANCE,
    UNMATCHED,
    MarketEvaluation,
    MarketLabel,
    NoVigMarket,
    SideEvaluation,
    american_to_decimal,
    american_to_implied_probability,
    decimal_to_implied_probability,
    edge,
    evaluate_benchmark,
    evaluate_pregame,
    expected_value,
    no_vig_two_way,
    opening_market_benchmark_from_archive,
    snapshot_is_pregame_valid,
)

__all__ = [
    "AMBIGUOUS",
    "MATCHED",
    "PROBABILITY_SUM_TOLERANCE",
    "UNMATCHED",
    "MarketEvaluation",
    "MarketLabel",
    "NoVigMarket",
    "SideEvaluation",
    "american_to_decimal",
    "american_to_implied_probability",
    "decimal_to_implied_probability",
    "edge",
    "evaluate_benchmark",
    "evaluate_pregame",
    "expected_value",
    "no_vig_two_way",
    "opening_market_benchmark_from_archive",
    "snapshot_is_pregame_valid",
]
