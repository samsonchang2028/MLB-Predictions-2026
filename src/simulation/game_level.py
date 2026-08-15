"""Game-level team score Monte Carlo (SIM-000).

Public contract (stable for SIM-001 / SIM-002)
------------------------------------------------
* :class:`SimulationConfig` — trial count, RNG seed, optional trial storage.
* :class:`GameSimulationResult` — per-game simulated score summaries.
* :func:`simulate_game` — one game's trials from pregame Gold ``features``.
* :func:`simulate_games` — batch wrapper over Gold-style rows.

Inputs are the same flat ``row["features"]`` dict produced by
``features.build.build_feature_matrix``. Only pregame columns are read; realized
scores must never appear in ``features``.

Tie handling
------------
``p_home_win`` treats a tied trial as **half** a home win:

``p_home_win = (home_wins + 0.5 * ties) / n_trials``

So a pure tie game across all trials yields ``0.5``, and integer-run ties
(home_runs == away_runs) contribute 0.5 to the home-win probability mass.

Score model
-----------
Callers fit :class:`~simulation.score_model.ScoreModel` once on a chronological
training partition (2021–2025 development seasons only; never 2026 holdout) and
pass the fitted model into :func:`simulate_game` / :func:`simulate_games`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from simulation.score_model import ScoreModel


@dataclass(frozen=True)
class SimulationConfig:
    """Monte Carlo trial settings."""

    n_trials: int = 10_000
    random_state: int = 0
    store_trials: bool = False


@dataclass(frozen=True)
class GameSimulationResult:
    """Summaries from one game's Monte Carlo trials."""

    game_pk: int
    p_home_win: float
    home_runs_mean: float
    away_runs_mean: float
    total_runs_mean: float
    home_runs_trials: tuple[int, ...] | None = None
    away_runs_trials: tuple[int, ...] | None = None


def simulate_game(
    features: Mapping[str, float],
    *,
    score_model: ScoreModel,
    config: SimulationConfig = SimulationConfig(),
    game_pk: int | None = None,
) -> GameSimulationResult:
    """Simulate final team scores for one game.

    Parameters
    ----------
    features:
        Pregame Gold feature mapping for the game.
    score_model:
        Fitted :class:`~simulation.score_model.ScoreModel` (training path is
        separate; see :func:`~simulation.score_model.fit_score_model`).
    config:
        Trial count and RNG seed. ``n_trials`` must be >= 1.
    game_pk:
        Optional identifier echoed on the result (defaults to ``0`` when absent).
    """
    _validate_config(config)
    rng = np.random.default_rng(config.random_state)
    home, away = score_model.sample_runs(
        features, n_trials=config.n_trials, rng=rng
    )
    return _summarize_trials(
        game_pk if game_pk is not None else 0,
        home,
        away,
        store_trials=config.store_trials,
    )


def simulate_games(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    score_model: ScoreModel,
    config: SimulationConfig = SimulationConfig(),
) -> list[GameSimulationResult]:
    """Simulate a batch of games from Gold-style rows.

    Each row must include ``features`` and may include ``game_pk``. Rows are
    processed in order; when ``config.random_state`` is fixed, results are
    deterministic for a given row sequence because each game advances the RNG
    stream by one independent ``sample_runs`` draw block.
    """
    _validate_config(config)
    rng = np.random.default_rng(config.random_state)
    results: list[GameSimulationResult] = []
    for position, row in enumerate(feature_rows):
        if "features" not in row:
            raise ValueError(f"feature_rows[{position}] missing 'features'")
        features = row["features"]
        if not isinstance(features, Mapping):
            raise ValueError(f"feature_rows[{position}]['features'] must be a mapping")
        game_pk = row.get("game_pk", position)
        home, away = score_model.sample_runs(
            features, n_trials=config.n_trials, rng=rng
        )
        results.append(
            _summarize_trials(
                int(game_pk),
                home,
                away,
                store_trials=config.store_trials,
            )
        )
    return results


def _validate_config(config: SimulationConfig) -> None:
    if config.n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {config.n_trials}")


def _summarize_trials(
    game_pk: int,
    home: np.ndarray,
    away: np.ndarray,
    *,
    store_trials: bool,
) -> GameSimulationResult:
    home_wins = int(np.sum(home > away))
    ties = int(np.sum(home == away))
    n_trials = home.size
    p_home_win = (home_wins + 0.5 * ties) / n_trials
    home_mean = float(home.mean())
    away_mean = float(away.mean())
    return GameSimulationResult(
        game_pk=game_pk,
        p_home_win=p_home_win,
        home_runs_mean=home_mean,
        away_runs_mean=away_mean,
        total_runs_mean=home_mean + away_mean,
        home_runs_trials=tuple(int(x) for x in home) if store_trials else None,
        away_runs_trials=tuple(int(x) for x in away) if store_trials else None,
    )
