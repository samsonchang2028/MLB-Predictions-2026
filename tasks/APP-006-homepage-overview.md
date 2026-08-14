# APP-006 — user-friendly homepage overview

## Status

candidate

## Dependencies

- APP-004
- APP-005
- OBS-002

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Create a Streamlit homepage that gives non-technical users a clear snapshot of
the model, today’s prediction coverage, current results, and data freshness.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/APP-004-streamlit-deployment.md`
- `tasks/APP-005-game-detail-page.md`
- `tasks/OBS-002-result-enrichment-operator.md`

## Allowed files

- `streamlit_app.py`
- `src/app/`
- `pages/`
- `tests/unit/app/`
- `tasks/APP-006-homepage-overview.md`

## May modify if necessary

- `README.md`
- `tasks/index.md`
- `state/CURRENT.md`

## Do not modify

- model training/evaluation methodology
- prediction generation logic
- market/odds math

## Inputs

- `state/predictions/daily.jsonl`
- `state/predictions/journal.jsonl`
- `state/predictions/skipped.jsonl`
- `reports/experiments/v1-holdout-2026.json`
- `reports/experiments/v1-model-diagnostics.json`

## Outputs

- A homepage/landing page for Streamlit users.
- Display-only summary cards derived from existing artifacts.

## Requirements

1. Add a homepage that appears before the detailed daily board.
2. Show concise cards for:
   - latest prediction run date,
   - games predicted today,
   - games awaiting starters/odds,
   - finished games with result journal rows,
   - current win/loss/no-play summary for the selected slate,
   - model identity: V1 tuned XGBoost, expanding window, uncalibrated.
3. Include a plain-language data freshness section:
   - predictions last updated,
   - odds snapshot time,
   - results last refreshed,
   - artifact source path/backend.
4. Keep all numbers artifact-backed. Do not recompute model metrics from raw
   data inside the Streamlit page.
5. If artifacts are missing, show actionable instructions instead of a stack
   trace.

## Critical correctness constraints

- Do not claim betting advice or guaranteed returns.
- Do not use 2026 result data to update the locked V1 model.
- Any accuracy/win-rate summary must clearly be for displayed picks/results,
  not model training evidence.
- PASS/no-play rows must not be counted as won/lost plays.

## Acceptance criteria

- A non-technical user can understand what the app does within 30 seconds.
- Missing artifact files produce clear guidance.
- Homepage works without local DuckDB.
- Unit tests cover summary-card shaping logic and missing-artifact behavior.

## Required tests

- unit tests for homepage summary aggregation
- regression test that PASS rows are excluded from play win/loss counts
- missing artifact test

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- known limitations,
- whether screenshots/manual Streamlit smoke were performed.

## Implementation handoff

- Added `src/app/homepage.py` with artifact-backed summary helpers for the
  Streamlit landing page.
- Updated `streamlit_app.py` from a placeholder entrypoint into a user-facing
  homepage with summary cards, data freshness, model identity, holdout metrics,
  missing-artifact guidance, and artifact-source details.
- Added `tests/unit/app/test_homepage.py` covering latest-slate aggregation,
  PASS/no-play exclusion from play win/loss counts, freshness timestamps,
  holdout metric loading, and missing artifact behavior.
- Commands run:
  - `python.exe -m pytest tests\unit\app\test_homepage.py tests\unit\app\test_board.py`
  - `python.exe -m py_compile src\app\homepage.py streamlit_app.py`
- Test result: 21 passed.
- Known limitation: no manual Streamlit screenshot smoke was performed in this
  task handoff.

## Reviewer-fix handoff

- Fixed reviewer P1: homepage summary now collapses same-slate re-runs to the
  latest prediction per `game_pk` before counting plays, no-plays, finished
  predictions, and freshness timestamps, matching the Daily Predictions board.
- Fixed reviewer P2: homepage reuses `app.board.DEFAULT_EDGE_THRESHOLD` instead
  of duplicating `0.02`, and the top cards now expose "Awaiting starters/odds".
- Added regression coverage proving stale same-game predictions are not counted
  after a newer prediction exists.
- Commands run:
  - `python.exe -m pytest tests\unit\app\test_homepage.py tests\unit\app\test_board.py`
  - `python.exe -m py_compile src\app\homepage.py streamlit_app.py`
- Test result: 22 passed.
