"""Unit tests for market.play_policy."""

from __future__ import annotations

import pytest

from market.play_policy import (
    PolicySpec,
    build_candidate_grid,
    classify_game,
    edge_selected_side,
    evaluate_policy,
    is_crossover,
    policy_by_name,
    raw_model_side,
)


def _row(
    *,
    model_probability: float = 0.56,
    market_probability: float = 0.50,
    edge: float | None = None,
    home_american: int = -110,
    away_american: int = 100,
) -> dict:
    if edge is None:
        edge = model_probability - market_probability
    return {
        "game_pk": 1,
        "model_probability": model_probability,
        "market_probability": market_probability,
        "edge": edge,
        "home_american": home_american,
        "away_american": away_american,
    }


def test_raw_model_side_tie_resolves_home() -> None:
    assert raw_model_side(_row(model_probability=0.5, market_probability=0.5, edge=0.0)) == "HOME"


def test_edge_selected_side_sign() -> None:
    assert edge_selected_side(_row(edge=0.01)) == "HOME"
    assert edge_selected_side(_row(edge=-0.01)) == "AWAY"


def test_crossover_detected_when_edge_flips_side() -> None:
    row = _row(model_probability=0.51, market_probability=0.56, edge=-0.05)
    assert raw_model_side(row) == "HOME"
    assert edge_selected_side(row) == "AWAY"
    assert is_crossover(row) is True


def test_baseline_threshold_boundary_is_inclusive() -> None:
    policy = policy_by_name("baseline")
    row = _row(edge=0.02)
    decision = evaluate_policy(row, policy)
    assert decision.play is True
    assert decision.bet_side == "HOME"

    row_below = _row(edge=0.019999)
    assert evaluate_policy(row_below, policy).play is False


def test_no_crossover_blocks_crossover_rows() -> None:
    policy = PolicySpec(
        family="B",
        name="test_no_crossover",
        min_edge=0.02,
        require_no_crossover=True,
    )
    crossover = _row(model_probability=0.51, market_probability=0.56, edge=-0.05)
    assert evaluate_policy(crossover, policy).play is False

    aligned = _row(model_probability=0.56, market_probability=0.50, edge=0.06)
    assert evaluate_policy(aligned, policy).play is True


def test_consensus_requires_model_market_agreement() -> None:
    policy = PolicySpec(
        family="C",
        name="test_consensus",
        confidence_floor=0.52,
        require_consensus=True,
    )
    agree = _row(model_probability=0.56, market_probability=0.52, edge=0.04)
    assert evaluate_policy(agree, policy).play is True

    disagree = _row(model_probability=0.56, market_probability=0.44, edge=0.12)
    decision = evaluate_policy(disagree, policy)
    assert decision.play is False
    assert "MODEL_MARKET_DISAGREE" in decision.reasons


def test_bounded_edge_range_enforced() -> None:
    policy = PolicySpec(
        family="D",
        name="test_bounded",
        min_edge=0.02,
        max_edge=0.04,
        confidence_floor=0.52,
        require_consensus=True,
    )
    inside = _row(model_probability=0.56, market_probability=0.52, edge=0.03)
    assert evaluate_policy(inside, policy).play is True

    outside = _row(model_probability=0.60, market_probability=0.52, edge=0.08)
    assert evaluate_policy(outside, policy).play is False


def test_missing_odds_blocks_play() -> None:
    policy = policy_by_name("baseline")
    row = _row(edge=0.05)
    row["home_american"] = "bad"
    assert evaluate_policy(row, policy).play is False


def test_classify_game_alias() -> None:
    policy = policy_by_name("baseline")
    assert classify_game(_row(edge=0.03), policy).play is True


def test_candidate_grid_is_fixed_size() -> None:
    grid = build_candidate_grid()
    families = {policy.family for policy in grid}
    assert families == {"A", "B", "C", "D", "E", "F"}
    assert len(grid) == len({policy.name for policy in grid})


def test_policy_by_name_accepts_baseline_alias() -> None:
    assert policy_by_name("baseline").min_edge == 0.02


def test_policy_by_name_unknown_raises() -> None:
    with pytest.raises(KeyError):
        policy_by_name("does_not_exist")
