# SIM-003 — full Gold feature score model

## Status

`ready`

## Dependencies

- SIM-000 (merged — game-level Monte Carlo engine)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/SIM-003-full-gold-score-model`

## Goal

Upgrade the Poisson score model to use the **same full Gold feature column union**
as the locked XGBoost evaluator (`evaluation.runner.vectorize_matrix`), not the
8-column team run-rate subset from SIM-000.

SIM-000's public `simulate_game` / `simulate_games` API must remain stable.

## Read first

- `AGENTS.md`
- `tasks/SIM-000-game-level-simulation.md`
- `src/simulation/score_model.py`
- `src/evaluation/runner.py` (`vectorize_matrix`, `_feature_columns`)
- `scripts/rerun_repaired_experiment.py` (Gold build pattern)

## Allowed files

- `src/simulation/score_model.py`
- `src/simulation/__init__.py` (exports only)
- `tests/unit/simulation/test_game_level.py`
- `tests/unit/simulation/test_score_model_full_gold.py` (new)
- `tasks/SIM-003-full-gold-score-model.md`

## Do not modify

- `src/simulation/game_level.py` (unless a one-line import/export fix is required)
- `src/models/*`
- `scripts/daily_predictions.py`
- Streamlit

## Requirements

1. `fit_score_model(training_rows, *, feature_columns=None)` fits home/away
   Poisson regressors on the **sorted union** of Gold feature keys (same rules as
   `vectorize_matrix`: missing -> NaN, SimpleImputer mean on training fold only).
2. When `feature_columns` is omitted, derive from training rows the same way
   `_feature_columns` does in `evaluation/runner.py` (reuse that helper if
   exported, or duplicate minimally with a comment pointing to the source of truth).
3. **Exclude** target/score columns from features (`home_runs`/`away_runs` labels
   stay separate; never in `features` dict).
4. Inference `sample_runs` uses the fitted column list; schema mismatch raises
   clearly (same spirit as XGBoost inference schema checks).
5. Keep `_MIN_RATE` floor and independent Poisson draws per team.
6. Update/add tests:
   - full column union used when training rows carry >8 feature keys
   - changing a non-run-rate Gold column (e.g. starter ERA diff) shifts lambda
   - existing SIM-000 tests still pass
7. Document in module docstring that SIM-000's 8-feature list is superseded for
   production fitting; list is now dynamic from Gold.

## Acceptance criteria

- [ ] Score model uses full Gold vectorization contract
- [ ] `pytest tests/unit/simulation/ -q` passes
- [ ] No change to `GameSimulationResult` shape
- [ ] Handoff notes training row shape for PIPE-007

## Handoff

Report feature count used, tests run, and example lambda shift when starter
features change.
