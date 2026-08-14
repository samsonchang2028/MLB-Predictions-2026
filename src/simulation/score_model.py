"""Fit and sample expected team runs from pregame Gold features (SIM-000).

Assumptions
-----------
* Final scores are modeled as independent Poisson draws per team per trial.
* Each side uses a separate ``PoissonRegressor`` on a small, documented subset of
  pregame run-rate features (offense for the scoring team + opponent run
  prevention). Missing feature values are mean-imputed using training-partition
  statistics only.
* Training rows must supply realized ``home_runs`` / ``away_runs`` labels that
  are **not** present in the ``features`` mapping (Gold target isolation).
* Fitting is chronological-safe when callers pass only pregame feature rows
  from completed games; this module does not join scores into ``features``.

Feature columns used
--------------------
Home runs model (predict home team scoring):

* ``home_team_runs_scored_avg_before``
* ``home_team_runs_scored_avg_L7``
* ``away_team_runs_allowed_avg_before``
* ``away_team_runs_allowed_avg_L7``

Away runs model (predict away team scoring):

* ``away_team_runs_scored_avg_before``
* ``away_team_runs_scored_avg_L7``
* ``home_team_runs_allowed_avg_before``
* ``home_team_runs_allowed_avg_L7``
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline

HOME_RUNS_FEATURES: tuple[str, ...] = (
    "home_team_runs_scored_avg_before",
    "home_team_runs_scored_avg_L7",
    "away_team_runs_allowed_avg_before",
    "away_team_runs_allowed_avg_L7",
)

AWAY_RUNS_FEATURES: tuple[str, ...] = (
    "away_team_runs_scored_avg_before",
    "away_team_runs_scored_avg_L7",
    "home_team_runs_allowed_avg_before",
    "home_team_runs_allowed_avg_L7",
)

_MIN_RATE: float = 0.05


@dataclass
class ScoreModel:
    """Fitted Poisson run-rate models for home and away scoring."""

    home_model: Pipeline
    away_model: Pipeline
    home_feature_names: tuple[str, ...] = HOME_RUNS_FEATURES
    away_feature_names: tuple[str, ...] = AWAY_RUNS_FEATURES

    def expected_rates(self, features: Mapping[str, float]) -> tuple[float, float]:
        """Return ``(lambda_home, lambda_away)`` expected runs for one game."""
        home_x = _vectorize(features, self.home_feature_names)
        away_x = _vectorize(features, self.away_feature_names)
        lambda_home = float(self.home_model.predict(home_x)[0])
        lambda_away = float(self.away_model.predict(away_x)[0])
        return max(lambda_home, _MIN_RATE), max(lambda_away, _MIN_RATE)

    def sample_runs(
        self,
        features: Mapping[str, float],
        *,
        n_trials: int,
        rng: np.random.Generator,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized Poisson samples for ``(home_runs, away_runs)`` trials."""
        if n_trials < 1:
            raise ValueError(f"n_trials must be >= 1, got {n_trials}")
        lambda_home, lambda_away = self.expected_rates(features)
        home = rng.poisson(lambda_home, size=n_trials)
        away = rng.poisson(lambda_away, size=n_trials)
        return home, away


def fit_score_model(
    training_rows: Sequence[Mapping[str, Any]],
    *,
    random_state: int = 0,
) -> ScoreModel:
    """Fit home/away Poisson regressors from historical Gold rows + scores.

    Each training row must provide:

    * ``features`` — pregame Gold feature mapping (no score columns).
    * ``home_runs`` / ``away_runs`` — nonnegative integer final scores.

    Rows missing any required feature name raise ``ValueError`` at fit time so
    callers fail loudly instead of silently dropping signal.
    """
    if not training_rows:
        raise ValueError("training_rows must be non-empty")

    home_x = np.vstack(
        [_vectorize(row["features"], HOME_RUNS_FEATURES)[0] for row in training_rows]
    )
    away_x = np.vstack(
        [_vectorize(row["features"], AWAY_RUNS_FEATURES)[0] for row in training_rows]
    )
    home_y = np.asarray([_runs_label(row, "home_runs") for row in training_rows])
    away_y = np.asarray([_runs_label(row, "away_runs") for row in training_rows])

    return ScoreModel(
        home_model=_build_poisson_pipeline(random_state).fit(home_x, home_y),
        away_model=_build_poisson_pipeline(random_state).fit(away_x, away_y),
    )


def _build_poisson_pipeline(random_state: int) -> Pipeline:  # noqa: ARG001
    # PoissonRegressor is deterministic for fixed inputs; random_state is kept
    # on fit_score_model for API symmetry with SimulationConfig.
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="mean")),
            ("regressor", PoissonRegressor(alpha=1.0, max_iter=300)),
        ]
    )


def _vectorize(features: Mapping[str, float], names: Sequence[str]) -> np.ndarray:
    missing = [name for name in names if name not in features]
    if missing:
        raise ValueError(f"features missing required columns: {missing}")
    values: list[float] = []
    for name in names:
        value = features[name]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            values.append(np.nan)
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"feature {name!r} must be numeric, got {value!r}")
        else:
            values.append(float(value))
    return np.asarray([values], dtype=float)


def _runs_label(row: Mapping[str, Any], field: str) -> int:
    if field not in row:
        raise ValueError(f"training row missing {field!r}")
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a non-negative int, got {value!r}")
    if value < 0:
        raise ValueError(f"{field} must be non-negative, got {value}")
    return value
