# APP-008 — best plays of the day chart

## Status

candidate (implemented; awaiting independent review/tester)

## Dependencies

- APP-007

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Add a simple visual section showing the strongest model-vs-market differences
for the selected slate.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/APP-007-friendly-daily-picks.md`
- `tasks/MARKET-001-market-engine.md`

## Allowed files

- `src/app/`
- `tests/unit/app/`
- `tasks/APP-008-best-plays-chart.md`

## May modify if necessary

- `pages/`
- `requirements.txt` only if an existing plotting dependency is insufficient
- `tasks/index.md`
- `state/CURRENT.md`

## Do not modify

- model training/evaluation
- daily prediction generation
- market engine math

## Inputs

- daily board rows shaped by APP-007

## Outputs

- A chart or ranked visual for top model-market differences.

## Requirements

1. Add a “Best plays of the day” section.
2. Rank by absolute displayed edge/difference, but only label rows as PLAY if
   they meet the existing display threshold.
3. Show picked team, opponent, start time, difference, and result status.
4. Make the chart robust to:
   - no predictions,
   - all PASS rows,
   - missing result journal,
   - pending games.
5. Include a note that this ranking is display-only and not a staking system.
6. Prefer existing Streamlit/chart dependencies over adding a new plotting
   library.

## Critical correctness constraints

- Do not create a new betting policy.
- Do not compute ROI here.
- Do not hide rows with missing data silently; surface empty-state copy.

## Acceptance criteria

- Chart renders from artifact-backed rows without DuckDB.
- Chart ordering is deterministic.
- Empty slates show useful guidance.
- Tests cover ranking and empty-state shaping logic.

## Required tests

- unit tests for top-play ranking
- unit tests for all-PASS and no-data states

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- known limitations,
- whether chart was manually inspected.

## Implementation handoff

- Added `src/app/best_plays.py` to rank artifact-backed APP-007 board rows by
  absolute displayed model-market difference with deterministic tie-breaking.
- Added a "Best plays of the day" section to the Daily Predictions page with
  display-only copy and an all-PASS empty-state message.
- The section preserves PASS labels; it does not create a staking policy,
  recompute ROI, or change prediction generation.
- Added focused unit tests for ranking, row limits, no-data, and all-PASS
  states.
- Manual Streamlit chart inspection was not performed in this task handoff.
