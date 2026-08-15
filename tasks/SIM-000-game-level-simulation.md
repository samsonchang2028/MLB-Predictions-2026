# SIM-000 — game-level team score Monte Carlo prototype

## Status

`ready`

## Dependencies

- FEAT-004 (Gold feature matrix)
- ML-010 (2026 holdout evaluated; simulation must not use 2026 for tuning)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/SIM-000-game-level-simulation`

## Goal

Add a **game-level** (not plate-appearance) Monte Carlo simulator that uses the
existing Gold pregame feature matrix to model **team final scores** and derive:

- `P(home_win)` from simulated trials
- per-team runs distribution (home/away)
- total-runs distribution (`home_runs + away_runs`)

No batter IDs, no lineups, no player props, no new ingestion.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/research/monte-carlo-simulation.md`
- `src/features/build.py`
- `src/evaluation/splits.py`
- `src/evaluation/runner.py` (reference only — sim is not sklearn-shaped)
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`

## Allowed files

- `src/simulation/__init__.py` (new)
- `src/simulation/game_level.py` (new — core engine)
- `src/simulation/score_model.py` (new — fit/sample expected runs; keep small)
- `tests/unit/simulation/test_game_level.py` (new)
- `tests/leakage/test_simulation_point_in_time.py` (new, if needed)
- `tasks/SIM-000-game-level-simulation.md`

## May modify if necessary

- `pyproject.toml` / `requirements.txt` only if a dependency is truly required
  (prefer stdlib + numpy/scipy already in stack)

## Do not modify

- `src/models/*` (locked V1 classifiers)
- `src/features/*` feature math
- `src/pipelines/daily.py` prediction contract
- `scripts/daily_predictions.py` (SIM-002 / later integration owns operator wiring)
- Streamlit pages
- ADR-006 or any V1 methodology lock

## Public contract (stable for SIM-001 / SIM-002)

Implement and document this API in `src/simulation/game_level.py`:

```python
@dataclass(frozen=True)
class SimulationConfig:
    n_trials: int = 10_000
    random_state: int = 0

@dataclass(frozen=True)
class GameSimulationResult:
    game_pk: int
    p_home_win: float
    home_runs_mean: float
    away_runs_mean: float
    total_runs_mean: float
    # optional: histogram or quantiles for totals if cheap to expose

def simulate_game(
    features: Mapping[str, float],
    *,
    config: SimulationConfig = SimulationConfig(),
) -> GameSimulationResult: ...

def simulate_games(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    config: SimulationConfig = SimulationConfig(),
) -> list[GameSimulationResult]: ...
```

`features` is the same flat dict shape as `row["features"]` from
`build_feature_matrix`. The score model may use a **subset** of columns but must
document which ones and remain point-in-time safe (pregame features only).

## Requirements

1. **Game-level only**: sample final `(home_runs, away_runs)` per trial. No PA
   loop, no batter tables.
2. **Score model**: fit expected home/away runs from historical Gold rows +
   realized scores (training path separate from inference). Use a simple
   count distribution (Poisson or negative binomial). Document assumptions.
3. **Trials**: default 10,000; deterministic given `random_state`.
4. **Outputs**: `p_home_win` = fraction of trials with `home_runs > away_runs`
   (handle ties explicitly — document rule, e.g. exclude ties or count as 0.5).
5. **Point-in-time**: simulation inputs are pregame Gold features only; no
   current-game scores in features. Add at least one unit test proving a
   post-game score cannot influence the same game's simulation inputs.
6. **No 2026 tuning**: any fitting for the score model uses 2021–2025 only in
   tests/scripts; do not inspect 2026 holdout.
7. **Tests**: deterministic trial with fixed seed; `p_home_win` in [0,1];
   means match trial aggregates; edge cases (0 trials raises, missing features
   raises clearly).
8. **Performance**: vectorized or batched trials; 10k trials per game must be
   fast enough for unit tests (< few seconds for small batch).

## Acceptance criteria

- [ ] `src/simulation/game_level.py` implements `simulate_game` / `simulate_games`
- [ ] Score model fits from historical feature rows + scores without leakage
- [ ] Unit tests pass: `pytest tests/unit/simulation/ -q`
- [ ] Public contract documented in module docstring for SIM-001/SIM-002
- [ ] No changes to locked V1 XGBoost path
- [ ] Handoff lists commands run and known limitations

## Handoff

Report task ID, summary, files changed, tests run, pass/fail, limitations,
and whether SIM-001/SIM-002 can proceed.
