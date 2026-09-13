"""APP-015 — display-only parlay leg ranking and combination helpers.

Ranks eligible slate legs with a composite parlay score (model confidence,
agreement, crossover/risk penalties — not raw edge alone), then suggests
2/3/4-leg combinations. **Experimental / entertainment only**; not PLAY
policy, not model validation, not ROI claims.

Combined win probability is a **naive independence estimate** (product of leg
model probabilities). MLB games are usually independent across matchups but
not guaranteed; no joint distribution is modeled in V1.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from market.engine import american_to_decimal, american_to_implied_probability
from market.play_policy import (
    edge_selected_side,
    is_crossover,
    model_market_agree,
    raw_model_side,
    selected_side_american,
    selected_side_model_probability,
)

DEFAULT_TOP_LEGS_POOL = 8
DEFAULT_SUGGESTED_PARLAYS = 3
DEFAULT_TOP_EDGE_LIMIT = 8

# Composite parlay leg score weights (balanced rank mode).
WEIGHT_MODEL_P = 0.40
WEIGHT_CONFIDENCE_MARGIN = 0.25
WEIGHT_AGREEMENT = 0.15
PENALTY_CROSSOVER = 0.20
PENALTY_RISK_FLAG = 0.10

PARLAY_DISCLAIMER = (
    "Experimental / entertainment only — not part of model validation or PLAY "
    "policy. Parlay win probability is a naive independence estimate; games "
    "are usually independent but not guaranteed."
)


class SourcePolicy(StrEnum):
    RAW_MODEL = "raw_model"
    PLAY = "play"
    TOP_EDGE = "top_edge"
    AGREEMENT_ONLY = "agreement_only"


class RankMode(StrEnum):
    MODEL_CONFIDENCE = "model_confidence"
    EXPECTED_PAYOUT = "expected_payout"
    BALANCED = "balanced"


SOURCE_POLICY_LABELS: dict[SourcePolicy, str] = {
    SourcePolicy.RAW_MODEL: "Raw Model",
    SourcePolicy.PLAY: "PLAY",
    SourcePolicy.TOP_EDGE: "Top Edge",
    SourcePolicy.AGREEMENT_ONLY: "Agreement Only",
}

RANK_MODE_LABELS: dict[RankMode, str] = {
    RankMode.MODEL_CONFIDENCE: "Highest model confidence",
    RankMode.EXPECTED_PAYOUT: "Highest expected payout",
    RankMode.BALANCED: "Balanced",
}


def _bet_side_for_policy(row: Mapping[str, Any], policy: SourcePolicy) -> str | None:
    if policy is SourcePolicy.RAW_MODEL or policy is SourcePolicy.AGREEMENT_ONLY:
        return raw_model_side(row)
    return edge_selected_side(row)


def _leg_has_valid_odds(row: Mapping[str, Any], side: str) -> bool:
    american = selected_side_american(row, side=side)
    if american is None:
        return False
    try:
        american_to_decimal(american)
    except ValueError:
        return False
    return True


def _confidence_margin(model_p: float) -> float:
    return abs(model_p - 0.5) * 2.0


def compute_parlay_leg_score(row: Mapping[str, Any], *, side: str, model_p: float) -> float:
    """Higher is a stronger parlay candidate under balanced ranking."""
    score = (
        model_p * WEIGHT_MODEL_P
        + _confidence_margin(model_p) * WEIGHT_CONFIDENCE_MARGIN
    )
    agree = model_market_agree(row)
    if agree is True:
        score += WEIGHT_AGREEMENT
    crossover = is_crossover(row)
    if crossover is True:
        score -= PENALTY_CROSSOVER
    flags = row.get("risk_flags") or []
    if isinstance(flags, list):
        score -= PENALTY_RISK_FLAG * len(flags)
    return score


def build_parlay_leg(row: Mapping[str, Any], *, side: str) -> dict[str, Any] | None:
    model_p = selected_side_model_probability(row, side=side)
    american = selected_side_american(row, side=side)
    if model_p is None or american is None:
        return None
    try:
        decimal_odds = american_to_decimal(american)
        implied_p = american_to_implied_probability(american)
    except ValueError:
        return None

    pick_label = row.get("pick")
    if not isinstance(pick_label, str) or not pick_label:
        pick_label = side

    flags = row.get("risk_flags") or []
    flag_text = ", ".join(flags) if isinstance(flags, list) and flags else "—"

    return {
        "game_pk": row.get("game_pk"),
        "matchup": row.get("matchup"),
        "pick": pick_label,
        "side": side,
        "first_pitch": row.get("game_start_pacific"),
        "model_p": model_p,
        "american": american,
        "decimal_odds": decimal_odds,
        "implied_p": implied_p,
        "parlay_score": compute_parlay_leg_score(row, side=side, model_p=model_p),
        "model_market_agree": row.get("model_market_agree"),
        "is_crossover": is_crossover(row),
        "risk_flags": flag_text,
        "edge": row.get("edge"),
        "play": bool(row.get("play")),
    }


def filter_eligible_legs(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_policy: SourcePolicy = SourcePolicy.RAW_MODEL,
    top_edge_limit: int = DEFAULT_TOP_EDGE_LIMIT,
) -> list[dict[str, Any]]:
    """Return shaped parlay legs passing the source policy and odds validity."""
    candidates: list[tuple[float, dict[str, Any]]] = []

    for row in rows:
        side = _bet_side_for_policy(row, source_policy)
        if side is None or not _leg_has_valid_odds(row, side):
            continue

        if source_policy is SourcePolicy.PLAY and not row.get("play"):
            continue
        if source_policy is SourcePolicy.AGREEMENT_ONLY:
            agree = model_market_agree(row)
            if agree is not True:
                continue

        leg = build_parlay_leg(row, side=side)
        if leg is None:
            continue

        sort_key = abs(float(row.get("edge") or 0.0))
        candidates.append((sort_key, leg))

    if source_policy is SourcePolicy.TOP_EDGE:
        candidates.sort(key=lambda item: (-item[0], item[1]["game_pk"] or 0))
        candidates = candidates[:top_edge_limit]

    return [leg for _, leg in candidates]


def rank_legs(
    legs: Sequence[Mapping[str, Any]],
    *,
    rank_mode: RankMode = RankMode.BALANCED,
) -> list[dict[str, Any]]:
    """Sort legs by the selected rank mode; tie-break game_pk ascending."""
    shaped = [dict(leg) for leg in legs]

    if rank_mode is RankMode.MODEL_CONFIDENCE:
        key_fn = lambda leg: (-float(leg["model_p"]), leg.get("game_pk") or 0)
    elif rank_mode is RankMode.EXPECTED_PAYOUT:
        key_fn = lambda leg: (-float(leg["decimal_odds"]), leg.get("game_pk") or 0)
    else:
        key_fn = lambda leg: (-float(leg["parlay_score"]), leg.get("game_pk") or 0)

    return sorted(shaped, key=key_fn)


def compute_parlay_metrics(
    legs: Sequence[Mapping[str, Any]],
    *,
    stake: float = 100.0,
) -> dict[str, Any]:
    """Combined decimal odds, payout, and naive independence estimate."""
    if not legs:
        return {
            "combined_decimal": None,
            "payout": None,
            "naive_independence_estimate": None,
            "leg_count": 0,
        }

    combined_decimal = 1.0
    naive_prob = 1.0
    for leg in legs:
        combined_decimal *= float(leg["decimal_odds"])
        naive_prob *= float(leg["model_p"])

    return {
        "combined_decimal": combined_decimal,
        "payout": combined_decimal * stake,
        "naive_independence_estimate": naive_prob,
        "leg_count": len(legs),
    }


def _combo_sort_key(combo: Sequence[Mapping[str, Any]]) -> tuple[float, tuple[Any, ...]]:
    score_sum = sum(float(leg["parlay_score"]) for leg in combo)
    game_pks = tuple(sorted(leg.get("game_pk") or 0 for leg in combo))
    return (-score_sum, game_pks)


def suggest_parlay_combinations(
    legs: Sequence[Mapping[str, Any]],
    *,
    leg_count: int,
    pool_size: int = DEFAULT_TOP_LEGS_POOL,
    limit: int = DEFAULT_SUGGESTED_PARLAYS,
    stake: float = 100.0,
) -> list[dict[str, Any]]:
    """Return top parlay combinations from the highest-ranked leg pool."""
    if leg_count < 2 or len(legs) < leg_count:
        return []

    pool = list(legs)[:pool_size]
    combos: list[tuple[tuple[float, tuple[Any, ...]], tuple[dict[str, Any], ...]]] = []

    for combo_tuple in itertools.combinations(pool, leg_count):
        combo = tuple(dict(leg) for leg in combo_tuple)
        combos.append((_combo_sort_key(combo), combo))

    combos.sort(key=lambda item: item[0])
    suggestions: list[dict[str, Any]] = []
    for index, (_, combo) in enumerate(combos[:limit], start=1):
        metrics = compute_parlay_metrics(combo, stake=stake)
        suggestions.append(
            {
                "rank": index,
                "legs": list(combo),
                "leg_labels": " + ".join(str(leg["pick"]) for leg in combo),
                **metrics,
            }
        )
    return suggestions


def build_parlay_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_policy: SourcePolicy = SourcePolicy.RAW_MODEL,
    rank_mode: RankMode = RankMode.BALANCED,
    leg_count: int = 2,
    pool_size: int = DEFAULT_TOP_LEGS_POOL,
    suggested_limit: int = DEFAULT_SUGGESTED_PARLAYS,
    stake: float = 100.0,
) -> dict[str, Any]:
    """Full parlay builder report for one slate."""
    if not rows:
        return {
            "status": "no_predictions",
            "disclaimer": PARLAY_DISCLAIMER,
            "eligible_legs": [],
            "ranked_legs": [],
            "suggestions": [],
            "source_policy": source_policy.value,
            "rank_mode": rank_mode.value,
            "leg_count": leg_count,
        }

    eligible = filter_eligible_legs(rows, source_policy=source_policy)
    if not eligible:
        return {
            "status": "no_eligible_legs",
            "disclaimer": PARLAY_DISCLAIMER,
            "eligible_legs": [],
            "ranked_legs": [],
            "suggestions": [],
            "source_policy": source_policy.value,
            "rank_mode": rank_mode.value,
            "leg_count": leg_count,
        }

    ranked = rank_legs(eligible, rank_mode=rank_mode)
    suggestions = suggest_parlay_combinations(
        ranked,
        leg_count=leg_count,
        pool_size=pool_size,
        limit=suggested_limit,
        stake=stake,
    )

    status = "ok"
    if len(ranked) < leg_count:
        status = "insufficient_legs"

    return {
        "status": status,
        "disclaimer": PARLAY_DISCLAIMER,
        "eligible_legs": eligible,
        "ranked_legs": ranked,
        "suggestions": suggestions,
        "source_policy": source_policy.value,
        "rank_mode": rank_mode.value,
        "leg_count": leg_count,
    }
