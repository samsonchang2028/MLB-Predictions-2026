"""Unit tests for totals market probabilities from simulation trials (SIM-002)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market import edge, no_vig_two_way
from simulation.game_level import SimulationConfig
from simulation.markets import (
    TotalsSimulationResult,
    evaluate_totals_pregame,
    simulate_totals,
    totals_probabilities_from_trials,
)
from simulation.score_model import ScoreModel, fit_score_model

HOME_TRIALS = (4, 5, 3, 6, 4)
AWAY_TRIALS = (4, 3, 5, 2, 5)
# totals = [8, 8, 8, 8, 9]


def _training_rows(n: int = 40) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        home_offense = 3.5 + (i % 5) * 0.3
        away_offense = 3.2 + (i % 4) * 0.25
        features = {
            "home_team_runs_scored_avg_before": home_offense,
            "home_team_runs_scored_avg_L7": home_offense - 0.1,
            "away_team_runs_scored_avg_before": away_offense,
            "away_team_runs_scored_avg_L7": away_offense - 0.1,
            "home_team_runs_allowed_avg_before": away_offense + 0.2,
            "home_team_runs_allowed_avg_L7": away_offense + 0.1,
            "away_team_runs_allowed_avg_before": home_offense + 0.2,
            "away_team_runs_allowed_avg_L7": home_offense + 0.1,
        }
        rows.append(
            {
                "features": features,
                "home_runs": int(round(home_offense + (i % 3))),
                "away_runs": int(round(away_offense + (i % 2))),
            }
        )
    return rows


@pytest.fixture
def score_model() -> ScoreModel:
    return fit_score_model(_training_rows(), random_state=0)


def test_totals_probabilities_half_line_8_5():
    p_over, p_under = totals_probabilities_from_trials(
        HOME_TRIALS, AWAY_TRIALS, line=8.5
    )
    assert p_over == pytest.approx(0.2)
    assert p_under == pytest.approx(0.8)
    assert p_over + p_under == pytest.approx(1.0)


def test_totals_probabilities_integer_line_push_excluded():
    p_over, p_under = totals_probabilities_from_trials(
        HOME_TRIALS, AWAY_TRIALS, line=8.0
    )
    assert p_over == pytest.approx(0.2)
    assert p_under == pytest.approx(0.0)
    assert p_over + p_under == pytest.approx(0.2)


def test_totals_probabilities_all_over():
    p_over, p_under = totals_probabilities_from_trials(
        (5, 6), (4, 5), line=8.0
    )
    assert p_over == pytest.approx(1.0)
    assert p_under == pytest.approx(0.0)


def test_totals_probabilities_all_under():
    p_over, p_under = totals_probabilities_from_trials(
        (1, 2), (1, 2), line=8.0
    )
    assert p_over == pytest.approx(0.0)
    assert p_under == pytest.approx(1.0)


def test_totals_probabilities_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="same length"):
        totals_probabilities_from_trials((1, 2), (1,), line=8.5)


def test_totals_probabilities_empty_raises():
    with pytest.raises(ValueError, match="at least one trial"):
        totals_probabilities_from_trials((), (), line=8.5)


def test_simulate_totals_deterministic(score_model: ScoreModel):
    features = _training_rows()[0]["features"]
    config = SimulationConfig(n_trials=2_000, random_state=99)
    first = simulate_totals(
        features,
        line=8.5,
        score_model=score_model,
        config=config,
        game_pk=42,
    )
    second = simulate_totals(
        features,
        line=8.5,
        score_model=score_model,
        config=config,
        game_pk=42,
    )
    assert first == second
    assert isinstance(first, TotalsSimulationResult)
    assert first.game_pk == 42
    assert first.line == 8.5
    assert 0.0 <= first.p_over <= 1.0
    assert 0.0 <= first.p_under <= 1.0


def test_totals_edge_sign_matches_market_conventions():
    p_over = 0.55
    p_under = 0.45
    market = no_vig_two_way(-110, -110)
    expected_over_edge = edge(p_over, market.no_vig_home_probability)
    expected_under_edge = edge(p_under, market.no_vig_away_probability)

    first_pitch = datetime(2026, 4, 10, 23, 0, tzinfo=timezone.utc)
    prediction = first_pitch - timedelta(hours=2)
    snapshot = prediction - timedelta(minutes=30)

    result = evaluate_totals_pregame(
        over_american=-110,
        under_american=-110,
        p_over=p_over,
        p_under=p_under,
        line=8.5,
        source="DraftKings",
        snapshot_timestamp=snapshot,
        prediction_timestamp=prediction,
        game_start_timestamp=first_pitch,
    )

    assert result.source == "DraftKings"
    assert result.snapshot_timestamp == snapshot
    assert result.line == 8.5
    assert result.over.edge == pytest.approx(expected_over_edge)
    assert result.under.edge == pytest.approx(expected_under_edge)
    assert result.over.edge == pytest.approx(0.05)
    assert result.under.edge == pytest.approx(-0.05)
    assert result.over.side == "over"
    assert result.under.side == "under"
