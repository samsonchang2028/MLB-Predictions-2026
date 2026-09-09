"""Deterministic PLAY policy functions for MARKET-006 / MARKET-007.

Pure helpers over a single prediction row (PIPE-001 shape). No I/O, no
mutation of stored artifacts. ``model_probability`` is ``P(home wins)``;
``edge`` is home-relative ``model_probability - market_probability``. The
edge-selected side follows the sign of ``edge``; the raw model favorite
follows the ``>= 0.5`` boundary (ties resolve to HOME).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

LARGE_DISAGREEMENT_THRESHOLD = 0.08
MARKET_NEUTRAL_BAND = 0.02

BASELINE_THRESHOLDS: tuple[float, ...] = (
    0.005,
    0.01,
    0.015,
    0.02,
    0.025,
    0.03,
    0.035,
    0.04,
    0.05,
    0.06,
)
CONSENSUS_CONFIDENCE_FLOORS: tuple[float, ...] = (0.52, 0.54, 0.55, 0.56, 0.58, 0.60)
BOUNDED_EDGE_RANGES_PP: tuple[tuple[float, float], ...] = (
    (0.01, 0.04),
    (0.01, 0.05),
    (0.01, 0.06),
    (0.01, 0.08),
    (0.02, 0.04),
    (0.02, 0.05),
    (0.02, 0.06),
    (0.02, 0.08),
    (0.03, 0.05),
    (0.03, 0.06),
    (0.03, 0.08),
)
NO_CROSSOVER_CAP_VALUES: tuple[float, ...] = (0.04, 0.05, 0.06, 0.08)
CONFIDENCE_FIRST_MIN_EDGE = 0.005


class PolicyReason(StrEnum):
    PLAY = "PLAY"
    PASS = "PASS"
    MODEL_MARKET_AGREE = "MODEL_MARKET_AGREE"
    MODEL_CONFIDENCE_ABOVE_FLOOR = "MODEL_CONFIDENCE_ABOVE_FLOOR"
    EDGE_WITHIN_VALIDATED_RANGE = "EDGE_WITHIN_VALIDATED_RANGE"
    CROSSOVER_BLOCKED = "CROSSOVER_BLOCKED"
    LARGE_DISAGREEMENT_BLOCKED = "LARGE_DISAGREEMENT_BLOCKED"
    EDGE_BELOW_THRESHOLD = "EDGE_BELOW_THRESHOLD"
    EDGE_ABOVE_CAP = "EDGE_ABOVE_CAP"
    MODEL_CONFIDENCE_TOO_LOW = "MODEL_CONFIDENCE_TOO_LOW"
    MODEL_MARKET_DISAGREE = "MODEL_MARKET_DISAGREE"
    MISSING_ODDS = "MISSING_ODDS"
    MISSING_MARKET = "MISSING_MARKET"
    MISSING_MODEL = "MISSING_MODEL"


POLICY_REASON_CODES: tuple[str, ...] = tuple(reason.value for reason in PolicyReason)


@dataclass(frozen=True)
class PolicySpec:
    family: str
    name: str
    min_edge: float | None = None
    max_edge: float | None = None
    confidence_floor: float | None = None
    require_no_crossover: bool = False
    require_consensus: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    play: bool
    bet_side: str | None
    reasons: tuple[str, ...]
    policy: PolicySpec


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def raw_model_side(row: Mapping[str, Any]) -> str | None:
    p_home = _number(row.get("model_probability"))
    if p_home is None:
        return None
    return "HOME" if p_home >= 0.5 else "AWAY"


def market_side(row: Mapping[str, Any]) -> str | None:
    p_home = _number(row.get("market_probability"))
    if p_home is None:
        return None
    return "HOME" if p_home >= 0.5 else "AWAY"


def edge_selected_side(row: Mapping[str, Any]) -> str | None:
    edge_value = _number(row.get("edge"))
    if edge_value is None:
        return None
    return "HOME" if edge_value >= 0.0 else "AWAY"


def is_crossover(row: Mapping[str, Any]) -> bool | None:
    model = raw_model_side(row)
    selected = edge_selected_side(row)
    if model is None or selected is None:
        return None
    return model != selected


def model_market_agree(row: Mapping[str, Any]) -> bool | None:
    model = raw_model_side(row)
    market = market_side(row)
    if model is None or market is None:
        return None
    return model == market


def selected_side_model_probability(row: Mapping[str, Any], *, side: str) -> float | None:
    p_home = _number(row.get("model_probability"))
    if p_home is None:
        return None
    return p_home if side == "HOME" else 1.0 - p_home


def selected_side_american(row: Mapping[str, Any], *, side: str) -> int | None:
    value = row.get("home_american") if side == "HOME" else row.get("away_american")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value


def abs_edge(row: Mapping[str, Any]) -> float | None:
    edge_value = _number(row.get("edge"))
    if edge_value is None:
        return None
    return abs(edge_value)


def classify_game(row: Mapping[str, Any], policy: PolicySpec) -> PolicyDecision:
    """Return a structured PLAY/PASS decision for one prediction row."""
    return evaluate_policy(row, policy)


def evaluate_policy(row: Mapping[str, Any], policy: PolicySpec) -> PolicyDecision:
    reasons: list[str] = []

    p_home = _number(row.get("model_probability"))
    if p_home is None:
        return PolicyDecision(False, None, (PolicyReason.MISSING_MODEL.value,), policy)

    market_home = _number(row.get("market_probability"))
    if market_home is None:
        return PolicyDecision(False, None, (PolicyReason.MISSING_MARKET.value,), policy)

    edge_value = _number(row.get("edge"))
    if edge_value is None:
        return PolicyDecision(False, None, (PolicyReason.MISSING_MODEL.value,), policy)

    model = raw_model_side(row)
    market = market_side(row)
    selected = edge_selected_side(row)
    assert model is not None and market is not None and selected is not None

    if policy.require_consensus:
        if model != market:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.MODEL_MARKET_DISAGREE.value,),
                policy,
            )
        reasons.append(PolicyReason.MODEL_MARKET_AGREE.value)
        bet_side = model
    elif policy.require_no_crossover or policy.family == "E":
        if is_crossover(row):
            return PolicyDecision(
                False,
                None,
                (PolicyReason.CROSSOVER_BLOCKED.value,),
                policy,
            )
        bet_side = selected
    else:
        bet_side = selected

    if policy.family == "F":
        bet_side = selected
        floor = policy.confidence_floor
        if floor is None:
            raise ValueError("confidence-first policy requires confidence_floor")
        side_p = selected_side_model_probability(row, side=bet_side)
        if side_p is None or side_p < floor:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.MODEL_CONFIDENCE_TOO_LOW.value,),
                policy,
            )
        reasons.append(PolicyReason.MODEL_CONFIDENCE_ABOVE_FLOOR.value)
        min_edge = policy.min_edge if policy.min_edge is not None else CONFIDENCE_FIRST_MIN_EDGE
        if abs(edge_value) < min_edge:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.EDGE_BELOW_THRESHOLD.value,),
                policy,
            )
    else:
        if policy.confidence_floor is not None:
            side_p = selected_side_model_probability(row, side=bet_side)
            if side_p is None or side_p < policy.confidence_floor:
                return PolicyDecision(
                    False,
                    None,
                    (PolicyReason.MODEL_CONFIDENCE_TOO_LOW.value,),
                    policy,
                )
            reasons.append(PolicyReason.MODEL_CONFIDENCE_ABOVE_FLOOR.value)

        if policy.min_edge is not None and abs(edge_value) < policy.min_edge:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.EDGE_BELOW_THRESHOLD.value,),
                policy,
            )

        if policy.max_edge is not None and abs(edge_value) > policy.max_edge:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.EDGE_ABOVE_CAP.value,),
                policy,
            )

        if policy.min_edge is not None and policy.max_edge is not None:
            reasons.append(PolicyReason.EDGE_WITHIN_VALIDATED_RANGE.value)

        if abs(p_home - market_home) >= LARGE_DISAGREEMENT_THRESHOLD and policy.family in {"C", "D"}:
            return PolicyDecision(
                False,
                None,
                (PolicyReason.LARGE_DISAGREEMENT_BLOCKED.value,),
                policy,
            )

    american = selected_side_american(row, side=bet_side)
    if american is None:
        return PolicyDecision(False, None, (PolicyReason.MISSING_ODDS.value,), policy)

    reasons.append(PolicyReason.PLAY.value)
    return PolicyDecision(True, bet_side, tuple(reasons), policy)


def build_candidate_grid() -> list[PolicySpec]:
    """Fixed, pre-declared candidate grid (no post-hoc mining)."""
    candidates: list[PolicySpec] = []

    for threshold in BASELINE_THRESHOLDS:
        candidates.append(
            PolicySpec(
                family="A",
                name=f"baseline_abs_edge_ge_{threshold:.3f}",
                min_edge=threshold,
            )
        )

    for threshold in BASELINE_THRESHOLDS:
        candidates.append(
            PolicySpec(
                family="B",
                name=f"no_crossover_abs_edge_ge_{threshold:.3f}",
                min_edge=threshold,
                require_no_crossover=True,
            )
        )

    for floor in CONSENSUS_CONFIDENCE_FLOORS:
        candidates.append(
            PolicySpec(
                family="C",
                name=f"consensus_conf_floor_{floor:.2f}",
                confidence_floor=floor,
                require_consensus=True,
            )
        )

    for floor in CONSENSUS_CONFIDENCE_FLOORS:
        for lo, hi in BOUNDED_EDGE_RANGES_PP:
            candidates.append(
                PolicySpec(
                    family="D",
                    name=f"consensus_bounded_{lo:.2f}_{hi:.2f}_floor_{floor:.2f}",
                    min_edge=lo,
                    max_edge=hi,
                    confidence_floor=floor,
                    require_consensus=True,
                )
            )

    for threshold in BASELINE_THRESHOLDS:
        for cap in NO_CROSSOVER_CAP_VALUES:
            if threshold > cap:
                continue
            candidates.append(
                PolicySpec(
                    family="E",
                    name=f"no_crossover_cap_{cap:.2f}_min_{threshold:.3f}",
                    min_edge=threshold,
                    max_edge=cap,
                    require_no_crossover=True,
                )
            )

    for floor in CONSENSUS_CONFIDENCE_FLOORS:
        candidates.append(
            PolicySpec(
                family="F",
                name=f"confidence_first_floor_{floor:.2f}",
                confidence_floor=floor,
                min_edge=CONFIDENCE_FIRST_MIN_EDGE,
            )
        )

    return candidates


def policy_by_name(name: str, *, grid: Sequence[PolicySpec] | None = None) -> PolicySpec:
    policies = list(grid) if grid is not None else build_candidate_grid()
    for policy in policies:
        if policy.name == name:
            return policy
    baseline = PolicySpec(family="A", name="baseline_abs_edge_ge_0.020", min_edge=0.02)
    if name in {"baseline", "baseline_abs_edge_ge_0.020"}:
        return baseline
    raise KeyError(f"unknown policy: {name}")


def policy_reason_codes() -> tuple[str, ...]:
    return POLICY_REASON_CODES
