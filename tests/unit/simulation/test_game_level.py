"""Unit tests for game-level Monte Carlo simulation (SIM-000)."""

from __future__ import annotations

import numpy as np
import pytest

from simulation.game_level import (
    GameSimulationResult,
    SimulationConfig,
    simulate_game,
    simulate_games,
)
from simulation.score_model import (
    AWAY_RUNS_FEATURES,
    HOME_RUNS_FEATURES,
    ScoreModel,
    fit_score_model,
)


def _base_features(**overrides: float) -> dict[str, float]:
    features = {
        "home_team_runs_scored_avg_before": 4.5,
        "home_team_runs_scored_avg_L7": 4.2,
        "away_team_runs_scored_avg_before": 4.0,
        "away_team_runs_scored_avg_L7": 3.8,
        "home_team_runs_allowed_avg_before": 4.1,
        "home_team_runs_allowed_avg_L7": 4.0,
        "away_team_runs_allowed_avg_before": 4.3,
        "away_team_runs_allowed_avg_L7": 4.1,
        # Post-game score columns must never influence simulation inputs.
        "home_final_score": 7.0,
        "away_final_score": 2.0,
    }
    features.update(overrides)
    return features


def _training_rows(n: int = 40) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        home_offense = 3.5 + (i % 5) * 0.3
        away_offense = 3.2 + (i % 4) * 0.25
        features = _base_features(
            home_team_runs_scored_avg_before=home_offense,
            home_team_runs_scored_avg_L7=home_offense - 0.1,
            away_team_runs_scored_avg_before=away_offense,
            away_team_runs_scored_avg_L7=away_offense - 0.1,
            home_team_runs_allowed_avg_before=away_offense + 0.2,
            home_team_runs_allowed_avg_L7=away_offense + 0.1,
            away_team_runs_allowed_avg_before=home_offense + 0.2,
            away_team_runs_allowed_avg_L7=home_offense + 0.1,
        )
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


def test_fit_and_simulate_deterministic_with_fixed_seed(score_model: ScoreModel):
    features = _base_features()
    config = SimulationConfig(n_trials=5_000, random_state=42)
    first = simulate_game(features, score_model=score_model, config=config, game_pk=99)
    second = simulate_game(features, score_model=score_model, config=config, game_pk=99)
    assert first == second


def test_p_home_win_in_unit_interval(score_model: ScoreModel):
    result = simulate_game(
        _base_features(),
        score_model=score_model,
        config=SimulationConfig(n_trials=2_000, random_state=1),
        game_pk=1,
    )
    assert 0.0 <= result.p_home_win <= 1.0


def test_means_match_trial_aggregates(score_model: ScoreModel):
    config = SimulationConfig(n_trials=3_000, random_state=7, store_trials=True)
    result = simulate_game(
        _base_features(),
        score_model=score_model,
        config=config,
        game_pk=5,
    )
    assert result.home_runs_trials is not None
    assert result.away_runs_trials is not None
    assert result.home_runs_mean == pytest.approx(
        sum(result.home_runs_trials) / len(result.home_runs_trials)
    )
    assert result.away_runs_mean == pytest.approx(
        sum(result.away_runs_trials) / len(result.away_runs_trials)
    )
    assert result.total_runs_mean == pytest.approx(
        result.home_runs_mean + result.away_runs_mean
    )


def test_tie_counts_as_half_home_win(score_model: ScoreModel):
    model = ScoreModel(
        home_model=score_model.home_model,
        away_model=score_model.away_model,
    )
    home = np.array([3, 3, 3, 4, 2], dtype=int)
    away = np.array([3, 3, 3, 1, 5], dtype=int)
    def _fixed_sample(_features, *, n_trials, rng):  # noqa: ARG001
        assert n_trials == home.size
        return home, away

    model.sample_runs = _fixed_sample  # type: ignore[method-assign]
    result = simulate_game(
        _base_features(),
        score_model=model,
        config=SimulationConfig(n_trials=5, random_state=0),
    )
    # 3 ties -> 1.5, 1 home win -> 2.5 / 5 = 0.5
    assert result.p_home_win == pytest.approx(0.5)


def test_zero_trials_raises(score_model: ScoreModel):
    with pytest.raises(ValueError, match="n_trials"):
        simulate_game(
            _base_features(),
            score_model=score_model,
            config=SimulationConfig(n_trials=0),
        )


def test_missing_features_raises_clearly(score_model: ScoreModel):
    broken = {k: v for k, v in _base_features().items() if k != "home_team_runs_scored_avg_L7"}
    with pytest.raises(ValueError, match="missing required columns"):
        simulate_game(broken, score_model=score_model, config=SimulationConfig(n_trials=10))


def test_post_game_scores_do_not_change_simulation(score_model: ScoreModel):
    base = _base_features()
    mutated = _base_features(home_final_score=99.0, away_final_score=0.0)
    config = SimulationConfig(n_trials=1_000, random_state=11)
    assert simulate_game(base, score_model=score_model, config=config) == simulate_game(
        mutated, score_model=score_model, config=config
    )


def test_simulate_games_batch(score_model: ScoreModel):
    rows = [
        {"game_pk": 10, "features": _base_features()},
        {"game_pk": 20, "features": _base_features(home_team_runs_scored_avg_before=5.5)},
    ]
    results = simulate_games(
        rows,
        score_model=score_model,
        config=SimulationConfig(n_trials=500, random_state=3),
    )
    assert len(results) == 2
    assert all(isinstance(r, GameSimulationResult) for r in results)
    assert [r.game_pk for r in results] == [10, 20]


def test_fit_requires_labels():
    with pytest.raises(ValueError, match="home_runs"):
        fit_score_model([{"features": _base_features()}])


def test_documented_feature_subsets_are_stable():
    assert len(HOME_RUNS_FEATURES) == 4
    assert len(AWAY_RUNS_FEATURES) == 4
    assert set(HOME_RUNS_FEATURES).isdisjoint({"home_runs", "away_runs", "home_final_score"})


def test_expected_rates_respect_floor(score_model: ScoreModel):
    sparse = _base_features(
        home_team_runs_scored_avg_before=0.0,
        home_team_runs_scored_avg_L7=0.0,
        away_team_runs_allowed_avg_before=0.0,
        away_team_runs_allowed_avg_L7=0.0,
        away_team_runs_scored_avg_before=0.0,
        away_team_runs_scored_avg_L7=0.0,
        home_team_runs_allowed_avg_before=0.0,
        home_team_runs_allowed_avg_L7=0.0,
    )
    lambda_home, lambda_away = score_model.expected_rates(sparse)
    assert lambda_home >= 0.05
    assert lambda_away >= 0.05
