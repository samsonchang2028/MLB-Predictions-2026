# PIPE-007 — daily operator simulation artifacts

## Status

`ready`

## Dependencies

- SIM-003 (full Gold score model)
- SIM-002 (totals probabilities) — optional for `p_over`/`p_under` fields
- PIPE-005 (pregame detail refresh)
- PIPE-002 (local daily operator)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/PIPE-007-daily-simulation-artifacts`

## Goal

Extend `scripts/daily_predictions.py` to fit the full-Gold score model on
2021–2025 (same training boundary as XGBoost) and write per-game simulation
artifacts for the Streamlit simulation tab.

## Read first

- `AGENTS.md`
- `tasks/SIM-003-full-gold-score-model.md`
- `tasks/SIM-002-simulation-totals-markets.md`
- `scripts/daily_predictions.py`
- `src/simulation/game_level.py`
- `src/simulation/markets.py` (if SIM-002 merged)

## Allowed files

- `scripts/daily_predictions.py`
- `tests/unit/scripts/test_daily_predictions.py`
- `tasks/PIPE-007-daily-simulation-artifacts.md`

## May modify if necessary

- `src/simulation/__init__.py` (exports only)

## Do not modify

- `src/pipelines/daily.py` core prediction contract
- `src/models/*`
- Streamlit pages (APP-010 owns display)

## Outputs

Append/overwrite `state/predictions/simulation.jsonl` keyed by
`(run_date, game_pk)` with at minimum:

```json
{
  "run_date": "2026-08-13",
  "game_pk": 823915,
  "p_home_win": 0.53,
  "home_runs_mean": 4.5,
  "away_runs_mean": 4.2,
  "total_runs_mean": 8.7,
  "total_runs_median": 9.0,
  "n_trials": 10000,
  "model_version": "sim-game-level-v1",
  "build_id": "<gold build_id>"
}
```

If SIM-002 is available and a totals line exists in fetched odds, include
`totals_line`, `p_over`, `p_under`.

## Requirements

1. Fit score model once per operator run on 2021–2025 historical Gold rows
   (reuse feature build path already in operator where possible).
2. Simulate only games in `ready_schedule` with announced starters (same gate as
   XGBoost predictions).
3. Default `n_trials=10_000`; log `[simulation]` timing like other steps.
4. `--skip-simulation` flag for fast/offline runs.
5. Unit tests with injected score model / mocked fit (no full DuckDB in CI).
6. Do not slow operator unacceptably — log `[timing] simulation` step.

## Acceptance criteria

- [ ] Operator writes `simulation.jsonl` on successful run
- [ ] Skip flag works
- [ ] Tests pass
- [ ] ADR-006 XGBoost path unchanged

## Handoff

Report artifact path, sample row, timing, and fields for APP-010.
