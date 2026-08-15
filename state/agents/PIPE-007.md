# PIPE-007 agent status

| Field | Value |
|---|---|
| Task | PIPE-007 |
| Role | implementer |
| Status | CANDIDATE |
| Branch | `agent/PIPE-007-daily-simulation-artifacts` |
| Worktree | `../predictions-1-wt-PIPE-007` |
| Blocker | none |

## Summary

Wired full-Gold score-model fit (2021–2025 only) and 10k-trial Monte Carlo
simulation into `scripts/daily_predictions.py`. Operator writes
`state/predictions/simulation.jsonl` keyed by `(run_date, game_pk)` with
overwrite semantics. Optional totals fields (`totals_line`, `p_over`, `p_under`)
when comparison odds payload includes primary-book totals. `--skip-simulation`
bypasses fit/sim/write. XGBoost prediction path unchanged.

## Tests

```text
python -m pytest tests/unit/scripts/test_daily_predictions.py -q
28 passed
```

## Artifact

- Path: `state/predictions/simulation.jsonl`
- Key: `(run_date, game_pk)` overwrite (same contract as `game_features.jsonl`)

## Sample row

```json
{
  "build_id": "gold-build-xyz",
  "away_runs_mean": 4.2,
  "game_pk": 823915,
  "home_runs_mean": 4.5,
  "model_version": "sim-game-level-v1",
  "n_trials": 10000,
  "p_home_win": 0.53,
  "run_date": "2026-08-13",
  "total_runs_mean": 8.7,
  "total_runs_median": 9.0
}
```

With totals odds: add `totals_line`, `p_over`, `p_under`.

## Files changed

- `scripts/daily_predictions.py`
- `tests/unit/scripts/test_daily_predictions.py`

## Follow-ups

- Reviewer + Tester gates per task file.
- APP-010 consumes `simulation.jsonl` for Streamlit simulation tab.
