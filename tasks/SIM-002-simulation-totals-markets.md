# SIM-002 — totals / runs-per-game from simulation + market edge

## Status

`blocked` (until SIM-000 merges or exposes stable simulation contract)

## Dependencies

- SIM-000 (game-level simulation + total-runs distribution)
- MARKET-001 (`src/market/engine.py` edge/no-vig math)
- DATA-024 (totals odds ingestion) — for live edge; may stub in tests

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/SIM-002-simulation-totals-markets`

## Goal

Reduce SIM-000 trial output into **totals market probabilities**:

- `P(over line)`, `P(under line)` for a given total-runs line
- projected runs per game (mean/median from trials)
- edge vs sportsbook no-vig over/under probabilities using `src/market/engine.py`

No player props. No run-line required in this task (optional stretch if trivial).

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/SIM-000-game-level-simulation.md`
- `tasks/DATA-024-totals-odds-ingestion.md`
- `src/simulation/game_level.py`
- `src/market/engine.py`
- `docs/research/monte-carlo-simulation.md`

## Allowed files

- `src/simulation/markets.py` (new)
- `tests/unit/simulation/test_markets.py` (new)
- `tasks/SIM-002-simulation-totals-markets.md`

## May modify if necessary

- `src/simulation/__init__.py` (exports)
- `src/market/__init__.py` (exports only)

## Do not modify

- `src/models/*`
- `src/features/*`
- `scripts/daily_predictions.py` (follow-up task wires operator)
- Streamlit (APP task later)
- Moneyline XGBoost path

## Public contract

```python
@dataclass(frozen=True)
class TotalsSimulationResult:
    game_pk: int
    line: float
    p_over: float
    p_under: float
    total_runs_mean: float
    total_runs_median: float

def totals_probabilities_from_trials(
    home_runs: Sequence[int],
    away_runs: Sequence[int],
    *,
    line: float,
) -> tuple[float, float]: ...

def simulate_totals(
    features: Mapping[str, float],
    *,
    line: float,
    config: SimulationConfig = SimulationConfig(),
) -> TotalsSimulationResult: ...
```

Edge helper may wrap `market.engine` with simulated `p_over` vs book implied
probabilities; preserve bookmaker + snapshot timestamp when computing edge.

## Requirements

1. `P(over)` = fraction of trials where `home_runs + away_runs > line`;
   `P(under)` for `< line`; document push handling on exact line.
2. Expose mean/median total runs from trials.
3. Unit tests: known trial vectors → exact over/under fractions; line 8.5 with
   integer totals; edge sign matches MARKET-001 conventions.
4. No recomputation of American odds math — reuse `src/market/engine.py`.
5. Deterministic given `random_state` from SIM-000.

## Acceptance criteria

- [ ] `src/simulation/markets.py` implements totals probabilities + edge helper
- [ ] Unit tests pass: `pytest tests/unit/simulation/test_markets.py -q`
- [ ] Documented push/tie rule for exact integer lines
- [ ] No player props or PA-level code

## Handoff

Report API, tests, example over/under probs for a fixture game, and follow-up
needed to wire daily operator + Streamlit.
