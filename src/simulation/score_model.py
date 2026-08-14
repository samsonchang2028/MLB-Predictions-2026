"""Fit and sample expected team runs from pregame Gold features (SIM-000 / SIM-003).

Assumptions
-----------
* Final scores are modeled as independent Poisson draws per team per trial.
* Each side uses a separate ``PoissonRegressor`` on the **full sorted union** of
  Gold feature keys observed in the training partition (same contract as
  :func:`evaluation.runner.vectorize_matrix`). Missing values are mean-imputed
  using training-partition statistics only.
* Training rows must supply realized ``home_runs`` / ``away_runs`` labels that
  are **not** present in the ``features`` mapping (Gold target isolation).
* Fitting is chronological-safe when callers pass only pregame feature rows
  from completed games; this module does not join scores into ``features``.

Feature columns
---------------
SIM-000's fixed 8-column run-rate subsets (``HOME_RUNS_FEATURES`` /
``AWAY_RUNS_FEATURES``) are retained for documentation only; production fitting
derives the column list dynamically from Gold rows via
:func:`evaluation.runner._feature_columns`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import Pipeline

from evaluation.runner import _as_float, _feature_columns

# SIM-000 legacy subsets — superseded by dynamic Gold union (SIM-003).
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
    feature_names: tuple[str, ...]

    def expected_rates(self, features: Mapping[str, float]) -> tuple[float, float]:
        """Return ``(lambda_home, lambda_away)`` expected runs for one game."""
        x = _vectorize(features, self.feature_names)
        lambda_home = float(self.home_model.predict(x)[0])
        lambda_away = float(self.away_model.predict(x)[0])
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
    feature_columns: Sequence[str] | None = None,
    random_state: int = 0,
) -> ScoreModel:
    """Fit home/away Poisson regressors from historical Gold rows + scores.

    Each training row must provide:

    * ``features`` — pregame Gold feature mapping (no score columns).
    * ``home_runs`` / ``away_runs`` — nonnegative integer final scores.

    When ``feature_columns`` is omitted, the sorted union of feature keys across
    ``training_rows`` is derived the same way as
    :func:`evaluation.runner.vectorize_matrix` (via ``_feature_columns``).
    """
    if not training_rows:
        raise ValueError("training_rows must be non-empty")

    columns = (
        list(feature_columns)
        if feature_columns is not None
        else _feature_columns(training_rows)
    )
    if not columns:
        raise ValueError("training_rows have no feature keys")

    x = _training_feature_matrix(training_rows, columns)
    home_y = np.asarray([_runs_label(row, "home_runs") for row in training_rows])
    away_y = np.asarray([_runs_label(row, "away_runs") for row in training_rows])

    return ScoreModel(
        home_model=_build_poisson_pipeline(random_state).fit(x, home_y),
        away_model=_build_poisson_pipeline(random_state).fit(x, away_y),
        feature_names=tuple(columns),
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


def _training_feature_matrix(
    training_rows: Sequence[Mapping[str, Any]],
    feature_columns: Sequence[str],
) -> np.ndarray:
    """Build ``X`` for score-model fitting (missing / non-numeric -> ``NaN``)."""
    col_index = {col: j for j, col in enumerate(feature_columns)}
    x = np.full((len(training_rows), len(feature_columns)), np.nan, dtype=float)
    for i, row in enumerate(training_rows):
        features = row.get("features", {}) or {}
        for col, j in col_index.items():
            numeric = _as_float(features.get(col))
            if numeric is not None:
                x[i, j] = numeric
    return x


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
