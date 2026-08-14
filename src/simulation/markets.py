"""Totals market probabilities from simulation trials (SIM-002).

Reduces SIM-000 trial output into over/under probabilities for a total-runs
line, plus optional model-vs-market edge via :mod:`market.engine`.

Push / tie rule (exact integer lines)
-------------------------------------
Trial totals are integer-valued (``home_runs + away_runs``). For a line
``L``:

* **Over** counts trials with total ``> L``.
* **Under** counts trials with total ``< L``.
* **Push** trials with total ``== L`` are excluded from both over and under
  numerators; each probability is ``count / n_trials`` over the full trial
  set. On half-lines (e.g. ``8.5``) pushes cannot occur. On integer lines
  (e.g. ``8.0``), ``p_over + p_under`` may be less than ``1.0`` when pushes
  exist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from market.engine import (
    MarketLabel,
    NoVigMarket,
    SideEvaluation,
    american_to_decimal,
    edge,
    expected_value,
    no_vig_two_way,
    snapshot_is_pregame_valid,
)
from simulation.game_level import SimulationConfig, simulate_game
from simulation.score_model import ScoreModel


@dataclass(frozen=True)
class TotalsSimulationResult:
    """Totals-market summaries from one game's Monte Carlo trials."""

    game_pk: int
    line: float
    p_over: float
    p_under: float
    total_runs_mean: float
    total_runs_median: float


@dataclass(frozen=True)
class TotalsMarketEvaluation:
    """Model-vs-market view for a totals line with provenance preserved.

    ``source`` is the bookmaker identity and ``snapshot_timestamp`` is the odds
    instant used for live pregame evaluation.
    """

    label: MarketLabel
    source: str
    snapshot_timestamp: datetime | None
    line: float
    market: NoVigMarket
    over: SideEvaluation
    under: SideEvaluation


def totals_probabilities_from_trials(
    home_runs: Sequence[int],
    away_runs: Sequence[int],
    *,
    line: float,
) -> tuple[float, float]:
    """Return ``(p_over, p_under)`` from aligned home/away trial vectors.

    See module docstring for push handling on exact integer lines.
    """
    if len(home_runs) != len(away_runs):
        raise ValueError(
            "home_runs and away_runs must have the same length, "
            f"got {len(home_runs)} and {len(away_runs)}"
        )
    n_trials = len(home_runs)
    if n_trials < 1:
        raise ValueError("at least one trial is required")
    over_count = 0
    under_count = 0
    for home, away in zip(home_runs, away_runs, strict=True):
        total = home + away
        if total > line:
            over_count += 1
        elif total < line:
            under_count += 1
    return over_count / n_trials, under_count / n_trials


def simulate_totals(
    features: Mapping[str, float],
    *,
    line: float,
    score_model: ScoreModel,
    config: SimulationConfig = SimulationConfig(),
    game_pk: int | None = None,
) -> TotalsSimulationResult:
    """Simulate total runs and derive over/under probabilities for ``line``.

    Runs :func:`~simulation.game_level.simulate_game` with ``store_trials=True``
    so trial vectors feed :func:`totals_probabilities_from_trials`. Results are
    deterministic for a fixed ``config.random_state`` (SIM-000).
    """
    trial_config = SimulationConfig(
        n_trials=config.n_trials,
        random_state=config.random_state,
        store_trials=True,
    )
    sim = simulate_game(
        features,
        score_model=score_model,
        config=trial_config,
        game_pk=game_pk,
    )
    if sim.home_runs_trials is None or sim.away_runs_trials is None:
        raise RuntimeError("simulate_game did not store trials")
    p_over, p_under = totals_probabilities_from_trials(
        sim.home_runs_trials,
        sim.away_runs_trials,
        line=line,
    )
    totals = np.asarray(sim.home_runs_trials, dtype=float) + np.asarray(
        sim.away_runs_trials, dtype=float
    )
    return TotalsSimulationResult(
        game_pk=sim.game_pk,
        line=line,
        p_over=p_over,
        p_under=p_under,
        total_runs_mean=sim.total_runs_mean,
        total_runs_median=float(np.median(totals)),
    )


def evaluate_totals_pregame(
    *,
    over_american: object,
    under_american: object,
    p_over: float,
    p_under: float,
    line: float,
    source: str,
    snapshot_timestamp: datetime,
    prediction_timestamp: datetime,
    game_start_timestamp: datetime,
    label: MarketLabel = MarketLabel.SNAPSHOT,
) -> TotalsMarketEvaluation:
    """Evaluate simulated totals probabilities against a live pregame market.

    Uses :func:`market.engine.no_vig_two_way` and :func:`market.engine.edge` for
    odds math. Over is mapped to the ``home`` slot and under to ``away`` in the
    two-way normalization. Preserves ``source`` and ``snapshot_timestamp``.
    """
    if not isinstance(label, MarketLabel):
        raise ValueError(f"label must be a MarketLabel, got {label!r}")
    if label is MarketLabel.OPENING:
        raise ValueError(
            "OPENING archive odds are a benchmark, not a live pregame input"
        )
    if snapshot_timestamp is None:
        raise ValueError("a pregame totals market requires a snapshot_timestamp")
    if not snapshot_is_pregame_valid(
        snapshot_timestamp, prediction_timestamp, game_start_timestamp
    ):
        raise ValueError(
            f"odds snapshot {snapshot_timestamp!r} is not timestamp-valid for a "
            f"pregame prediction (require snapshot < prediction "
            f"{prediction_timestamp!r} < first pitch {game_start_timestamp!r})"
        )
    market = no_vig_two_way(over_american, under_american)
    over, under = _build_totals_sides(market, p_over, p_under)
    return TotalsMarketEvaluation(
        label=label,
        source=_validate_source(source),
        snapshot_timestamp=snapshot_timestamp,
        line=line,
        market=market,
        over=over,
        under=under,
    )


def _build_totals_sides(
    market: NoVigMarket,
    p_over: float,
    p_under: float,
) -> tuple[SideEvaluation, SideEvaluation]:
    over = SideEvaluation(
        side="over",
        american=market.home_american,
        decimal_odds=american_to_decimal(market.home_american),
        no_vig_market_probability=market.no_vig_home_probability,
        model_probability=p_over,
        edge=edge(p_over, market.no_vig_home_probability),
        expected_value=expected_value(p_over, market.home_american),
    )
    under = SideEvaluation(
        side="under",
        american=market.away_american,
        decimal_odds=american_to_decimal(market.away_american),
        no_vig_market_probability=market.no_vig_away_probability,
        model_probability=p_under,
        edge=edge(p_under, market.no_vig_away_probability),
        expected_value=expected_value(p_under, market.away_american),
    )
    return over, under


def _validate_source(source: object) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ValueError(
            f"source/bookmaker identity must be a non-empty string, got {source!r}"
        )
    return source
