# APP-018 — Daily Monitoring Baseline vs Raw Model Comparison

## Status

candidate

## Dependencies

- APP-013 (dashboard analytics / Model Quality page)
- APP-014 (daily prospective monitoring — finalize or build atop if merged)

## Execution

Primary role: **implementer**

Review required: yes

Tester required: yes

Worktree required: yes — branch `agent/APP-018-monitoring-strategies`

## Goal

In **Model Quality → Daily Monitoring**, compare two selection strategies side
by side on each slate date and in aggregate:

| Strategy | Definition |
|----------|------------|
| **Baseline PLAY** | Edge-selected side when `abs(edge) >= 2%` (existing board PLAY rule) |
| **Raw model favorite** | Bet model favorite on **every** game (`P(home) >= 0.5` → home) |

User question: *"can we compare the baseline and also raw model picks"*

## Allowed files

- `src/app/dashboard_analytics.py`
- `src/app/model_quality_page.py`
- `tests/unit/app/test_dashboard_analytics.py`
- `tests/unit/app/test_model_quality_page.py` (if needed)
- `tasks/APP-018-daily-monitoring-strategy-comparison.md`
- `state/task-context/APP-018.md`
- `state/agents/APP-018.md`

## Do not modify

- `src/pipelines/`
- `src/market/engine.py` (use existing `expected_value`, `flat_stake_profit`)
- holdout / development experiment reports
- model training code

## Requirements

1. Extend `build_daily_monitoring_summary()` with per-date **raw model**
   fields parallel to existing `play_*` baseline fields:
   - count, wins, losses, pending, win rate, ROI, units, staked units, missing odds
2. Raw model side uses **probability boundary** (`model_predicted_home`), not
   edge-selected side. Use `raw_model_pick_american` / `raw_model_correct` on
   resolved rows — distinct from journal `correct` on crossover games.
3. Model-quality metrics (log loss, Brier, ECE) unchanged — all resolved preds.
4. Model Quality **Daily Monitoring** tab:
   - Aggregate comparison header (baseline vs raw model win rate, record, ROI, units)
   - Daily table columns for both strategies (rename PLAY columns to **Baseline**)
5. Caption clarifies baseline excludes PASS rows; raw model includes all games.
6. Unit tests: raw model pick uses P(home) boundary; crossover game shows different
   baseline vs raw outcome; existing daily monitoring tests still pass.

## Acceptance criteria

- Daily Monitoring shows both strategies for historical slates with journal results.
- Crossover games can differ between baseline W/L and raw model W/L (tested).
- Reviewer APPROVE; tester PASS on `test_dashboard_analytics.py`.

## Note for implementer

Orchestrator incorrectly patched `dashboard_analytics.py` and
`model_quality_page.py` directly during chat. Use any local diff as draft
reference only; run full review/test gates in an isolated worktree before merge.
