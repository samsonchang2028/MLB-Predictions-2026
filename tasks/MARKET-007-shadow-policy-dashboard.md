# MARKET-007 — Shadow policy implementation and dashboard observability

## Status

blocked (depends on MARKET-006)

## Dependencies

- MARKET-006 (policy module, study report, shadow candidate selection)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/MARKET-007-shadow-policy-dashboard`

## Goal

Wire MARKET-006 policy outputs into Streamlit as **observable shadow
classification** — without replacing baseline PLAY/PASS or presenting the
challenger as a betting recommendation before ADR-007 acceptance.

## Read first

- `AGENTS.md`, `state/CURRENT.md`, `state/task-context/MARKET-007.md`
- `tasks/MARKET-006-play-policy-study.md` handoff + report artifacts
- `src/market/play_policy.py` (from MARKET-006)
- `src/app/board.py`, `src/app/daily_board_page.py`, `src/app/signal_dashboard.py`
- `streamlit_app.py`, `pages/1_Daily_Predictions.py`

## Allowed files

- `src/app/play_policy_display.py` (new — thin display adapter over `market.play_policy`)
- `src/app/board.py` (additive shadow fields only — **do not change** `DEFAULT_EDGE_THRESHOLD` or baseline PLAY logic)
- `src/app/daily_board_page.py`, `src/app/signal_dashboard.py`
- `streamlit_app.py`, `pages/1_Daily_Predictions.py`, `pages/3_Market_Edge.py` if needed
- `tests/unit/app/test_play_policy_display.py`
- `state/task-context/MARKET-007.md`, `state/agents/MARKET-007.md`
- this task file

## May modify if necessary

- `src/app/betting_results_page.py` or `src/app/prospective_evaluation_page.py` for comparison panel
- `tasks/index.md`, `state/CURRENT.md`

## Do not modify

- `src/market/play_policy.py` core semantics (request changes via MARKET-006 fix pass)
- production PLAY rule / baseline threshold
- prediction artifacts, model, features, pipelines

## Requirements

Dashboard must show per game:

- Raw model favorite, model P(selected side)
- Market favorite, market P(selected side)
- Model-market agreement flag
- Edge, baseline legacy PLAY/PASS
- **Shadow policy decision** + policy reason codes + risk flags

Labels: `BASELINE PLAY`, `SHADOW CANDIDATE`, `NO CANDIDATE`, `CROSSOVER BLOCKED`,
`LARGE DISAGREEMENT REVIEW` — not "recommended bet".

Comparison panel (baseline vs shadow vs raw model favorite):

- N, win rate, ROI, units, average odds, pending, sample period — sample size prominent.

Shadow writes classification to **new read-side fields only** — no mutation of
`daily.jsonl` / journal history.

## Acceptance criteria

- Baseline PLAY unchanged (regression tests on `board.py` PLAY/PASS).
- Shadow policy uses MARKET-006 `play_policy` module deterministically.
- Every shadow decision has reason codes explainable in UI.
- Comparison metrics match MARKET-006 study population semantics.
- Focused + relevant app tests pass; full suite green.

## Handoff

Report dashboard surfaces changed, shadow policy version wired, sample comparison
numbers, tests run, known limitations, explicit note that production PLAY unchanged.
