# SIM-001 — validate simulated moneyline vs locked XGBoost

## Status

`blocked` (until SIM-000 merges or exposes stable `src/simulation/game_level.py` contract on integration branch)

## Dependencies

- SIM-000 (game-level simulation engine + public contract)
- ML-009 / ADR-006 (locked XGBoost baseline)
- ML-004 (walk-forward splits)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/SIM-001-simulation-ml-validation`

## Goal

Walk-forward evaluation comparing **simulated `P(home_win)`** (SIM-000) against
the **locked XGBoost** moneyline model on the same chronological folds and
primary metrics (log loss → Brier → ECE; ROC-AUC secondary).

This is **research/validation only** — do not replace ADR-006 or change V1
production predictions.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/SIM-000-game-level-simulation.md`
- `src/simulation/game_level.py` (after SIM-000)
- `src/evaluation/splits.py`
- `src/evaluation/runner.py`
- `src/experiments/expanding.py` (reference report shape)
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`

## Allowed files

- `src/simulation/evaluation.py` (new)
- `src/experiments/simulation_moneyline.py` (new)
- `scripts/run_simulation_ml_validation.py` (new, optional thin CLI)
- `tests/unit/simulation/test_evaluation.py` (new)
- `tasks/SIM-001-simulation-ml-validation.md`

## May modify if necessary

- `src/simulation/__init__.py` (exports only)

## Do not modify

- `src/models/*` locked classifier implementations
- `src/features/*`
- `scripts/daily_predictions.py`
- ADR-006
- Streamlit

## Requirements

1. Use the same expanding-window fold boundaries as existing experiments
   (`src/evaluation/splits.py`); 2026 excluded from selection.
2. For each test game: run simulation with **pregame features only**; record
   `p_home_win` and `y_true` (home_win).
3. Compute per-fold and aggregate: log loss, Brier, ECE, ROC-AUC (secondary),
   accuracy (secondary).
4. Run the **locked XGBoost** baseline on the same folds for apples-to-apples
   comparison table (reuse existing training path where possible).
5. Emit a JSON report under `reports/experiments/` (e.g.
   `v1-simulation-moneyline-<build_id>.json`) with the same general shape as
   `v1-repaired-a910017bac839af5.json` ranking section where practical.
6. Unit tests with tiny synthetic feature rows — no network, no full DuckDB
   required in CI.
7. Document in handoff: sim vs XGBoost winner on primary metrics; do **not**
   claim production replacement.

## Acceptance criteria

- [ ] Walk-forward comparison script runs on certified build fixture or documented local DB path
- [ ] Report JSON written with sim vs xgboost metrics
- [ ] Unit tests pass: `pytest tests/unit/simulation/test_evaluation.py -q`
- [ ] 2026 not used for tuning or model selection
- [ ] No change to V1 daily operator output

## Handoff

Report metrics table (sim vs XGBoost), report path, tests run, and recommendation
whether sim moneyline is competitive enough to surface in UI (comparison only).
