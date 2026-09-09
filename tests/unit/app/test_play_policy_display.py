"""Unit tests for MARKET-007 play_policy_display adapter."""

from __future__ import annotations

import json

import pytest

from app.board import DEFAULT_EDGE_THRESHOLD, load_daily_board
from app.play_policy_display import (
    BASELINE_POLICY,
    EXPLORATORY_CONSENSUS_POLICY,
    EXPLORATORY_NO_CROSSOVER_POLICY,
    SHADOW_NOT_PRODUCTION_NOTE,
    baseline_play_label,
    build_play_policy_comparison,
    enrich_board_row_with_shadow,
    shadow_status_label,
)
from market.play_policy import PolicyDecision, PolicyReason, PolicySpec, evaluate_policy


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _prediction(
    game_pk: int,
    *,
    p_home: float = 0.56,
    market_home: float = 0.50,
    edge: float | None = None,
    run_date: str = "2026-08-14",
    home_american: int = -110,
    away_american: int = 100,
) -> dict:
    if edge is None:
        edge = p_home - market_home
    return {
        "game_pk": game_pk,
        "run_date": run_date,
        "model_probability": p_home,
        "market_probability": market_home,
        "edge": edge,
        "odds_snapshot_timestamp": "2026-08-14T19:55:00+00:00",
        "prediction_timestamp": "2026-08-14T20:00:00+00:00",
        "game_start_timestamp": "2026-08-15T02:05:00+00:00",
        "model_version": "v1",
        "home_team_id": 147,
        "away_team_id": 111,
        "home_american": home_american,
        "away_american": away_american,
    }


def _journal(prediction: dict, *, actual_home_win: bool = True) -> dict:
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "actual_home_win": actual_home_win,
        "correct": actual_home_win,
        "enrichment_timestamp": "2026-08-15T07:00:00+00:00",
    }


def test_baseline_play_label_uses_board_play_flag_not_play_policy_odds_gate() -> None:
    [board_row] = load_daily_board(_FakeStore([_prediction(1, edge=0.03)]))
    assert board_row["play"] is True
    assert baseline_play_label(board_row) == "BASELINE PLAY"

    no_odds_row = load_daily_board(_FakeStore([{**_prediction(2, edge=0.03), "home_american": None}]))[0]
    assert no_odds_row["play"] is True
    assert baseline_play_label(no_odds_row) == "BASELINE PLAY"


def test_shadow_status_labels() -> None:
    play = PolicyDecision(True, "HOME", (PolicyReason.PLAY.value,), BASELINE_POLICY)
    blocked = PolicyDecision(False, None, (PolicyReason.CROSSOVER_BLOCKED.value,), EXPLORATORY_NO_CROSSOVER_POLICY)
    large = PolicyDecision(False, None, (PolicyReason.LARGE_DISAGREEMENT_BLOCKED.value,), EXPLORATORY_CONSENSUS_POLICY)
    pass_decision = PolicyDecision(False, None, (PolicyReason.EDGE_BELOW_THRESHOLD.value,), BASELINE_POLICY)

    assert shadow_status_label(play) == "SHADOW CANDIDATE"
    assert shadow_status_label(blocked) == "CROSSOVER BLOCKED"
    assert shadow_status_label(large) == "LARGE DISAGREEMENT REVIEW"
    assert shadow_status_label(pass_decision) == "NO CANDIDATE"


def test_enrich_board_row_with_shadow_includes_reason_codes_and_flags() -> None:
    prediction = _prediction(1, p_home=0.51, market_home=0.56, edge=-0.05)
    [board_row] = load_daily_board(_FakeStore([prediction]))
    enriched = enrich_board_row_with_shadow(board_row, prediction)

    assert enriched["baseline_play_label"] == "BASELINE PLAY"
    assert enriched["raw_model_favorite"] == "HOME"
    assert enriched["market_favorite"] == "HOME"
    assert enriched["model_market_agree"] is True
    assert enriched["no_crossover_label"] == "CROSSOVER BLOCKED"
    assert "CROSSOVER_BLOCKED" in enriched["no_crossover_reason_codes"]
    assert "CROSSOVER" in enriched["risk_flags"]
    assert SHADOW_NOT_PRODUCTION_NOTE in enriched["shadow_note"]


def test_enrich_board_row_marks_consensus_shadow_candidate_when_agreeing() -> None:
    prediction = _prediction(1, p_home=0.56, market_home=0.52, edge=0.04)
    [board_row] = load_daily_board(_FakeStore([prediction]))
    enriched = enrich_board_row_with_shadow(board_row, prediction)

    assert enriched["consensus_label"] == "SHADOW CANDIDATE"
    assert PolicyReason.MODEL_MARKET_AGREE.value in enriched["consensus_reason_codes"]
    assert PolicyReason.PLAY.value in enriched["consensus_reason_codes"]


def test_build_play_policy_comparison_uses_resolved_latest_per_game_population(tmp_path) -> None:
    play = _prediction(1, edge=0.05)
    older = {**play, "prediction_timestamp": "2026-08-14T18:00:00+00:00", "edge": 0.01}
    newer = {**play, "prediction_timestamp": "2026-08-14T20:00:00+00:00", "edge": 0.05}
    journal = _journal(newer, actual_home_win=True)

    comparison = build_play_policy_comparison([older, newer], [journal])

    assert comparison["meta"]["n_resolved_latest_per_game"] == 1
    assert comparison["sample_period"] == "2026-08-14"
    assert comparison["baseline_edge_threshold"] == DEFAULT_EDGE_THRESHOLD
    assert SHADOW_NOT_PRODUCTION_NOTE in comparison["note"]
    assert len(comparison["display_rows"]) == 4
    baseline = comparison["strategies"]["baseline"]
    assert baseline["n_play"] == 1
    assert baseline["n_resolved"] == 1


def test_exploratory_policies_match_market_006_names() -> None:
    assert EXPLORATORY_NO_CROSSOVER_POLICY.name == "no_crossover_abs_edge_ge_0.020"
    assert EXPLORATORY_CONSENSUS_POLICY.name == "consensus_conf_floor_0.55"


def test_no_crossover_shadow_blocks_crossover_row() -> None:
    prediction = _prediction(1, p_home=0.51, market_home=0.56, edge=-0.05)
    decision = evaluate_policy(prediction, EXPLORATORY_NO_CROSSOVER_POLICY)
    assert decision.play is False
    assert shadow_status_label(decision) == "CROSSOVER BLOCKED"
