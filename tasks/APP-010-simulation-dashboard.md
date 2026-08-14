# APP-010 — Streamlit simulation comparison tab

## Status

`ready` (can implement against PIPE-007 contract; may use fixtures if PIPE-007 not merged yet)

## Dependencies

- PIPE-007 (simulation artifacts) OR stable `simulation.jsonl` contract from task
- APP-001 (daily board patterns)
- SIM-003 (full Gold sim — display labels)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `no` (display-only; unit tests for data loaders)

Worktree required: `yes` — branch `agent/APP-010-simulation-dashboard`

## Goal

Add a Streamlit page **Simulation** that compares, per game on the latest slate:

- **XGBoost** moneyline probability (from `daily.jsonl`)
- **Monte Carlo** moneyline probability (from `simulation.jsonl`)
- **Market** no-vig probability (from `daily.jsonl`)

Plus runs-per-game visuals: mean/median total runs, simple total-runs distribution
chart when trial histogram data is available or binned from stored quantiles.

## Read first

- `AGENTS.md`
- `tasks/PIPE-007-daily-simulation-artifacts.md`
- `src/app/daily_board_page.py`
- `src/app/board.py`
- `pages/1_Daily_Predictions.py` (wrapper pattern)

## Allowed files

- `src/app/simulation_page.py` (new)
- `src/app/simulation_board.py` (new — load/shape only, no metric math)
- `pages/5_Simulation.py` (new wrapper)
- `tests/unit/app/test_simulation_board.py` (new)
- `tasks/APP-010-simulation-dashboard.md`

## May modify if necessary

- `streamlit_app.py` (sidebar hint text only)

## Do not modify

- `src/models/*`
- `src/simulation/*` engine code
- `scripts/daily_predictions.py`
- Moneyline board semantics (`PLAY`/`PASS` rules on daily board unchanged)

## UI requirements

1. New sidebar page: **Simulation** (`pages/5_Simulation.py`).
2. Slate table columns: matchup, `P(home) XGB`, `P(home) Sim`, `P(home) Market`,
   sim projected total runs, disagreement flag when |XGB - Sim| > 5pp.
3. **Charts** (Streamlit native — `st.bar_chart` / `st.line_chart` / histogram):
   - Slate-level: grouped bar comparing the three P(home) sources for each game
   - Per-game expander: total runs distribution (binned bar chart from
     `total_runs_mean` + stored quantiles or histogram bins if PIPE-007 provides)
4. Clear caption: simulation is **research/V2**; XGBoost remains ADR-006 moneyline
   lock; sim drives totals/runs view.
5. Graceful empty state when `simulation.jsonl` missing.
6. Pacific time for timestamps where shown.

## Acceptance criteria

- [ ] Page loads via `streamlit run streamlit_app.py`
- [ ] Loader unit tests pass
- [ ] No probability recomputation in app layer — read artifacts only
- [ ] Works with committed sample JSONL fixtures in tests

## Handoff

Screenshot description, files changed, tests run, and any PIPE-007 contract gaps.
