# APP-007 — friendlier daily picks board

## Status

backlog

## Dependencies

- APP-006
- OBS-002

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Make the daily picks board readable for users who do not know model-probability,
no-vig, edge, or feature terminology.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/APP-003-daily-board-display.md`
- `tasks/APP-006-homepage-overview.md`
- `tasks/OBS-002-result-enrichment-operator.md`

## Allowed files

- `src/app/board.py`
- `src/app/daily_board_page.py`
- `tests/unit/app/test_board.py`
- `tasks/APP-007-friendly-daily-picks.md`

## May modify if necessary

- `pages/`
- `README.md`
- `tasks/index.md`
- `state/CURRENT.md`

## Do not modify

- prediction generation
- model probabilities
- market calculations
- journal correctness semantics

## Inputs

- existing board rows from `app.board`
- daily prediction artifacts
- OBS-002 result journal

## Outputs

- A simplified daily picks table.
- Plain-English labels/tooltips for model side and result status.

## Requirements

1. Reframe the table around the picked team, not always the home team.
2. Add display fields such as:
   - `Pick`,
   - `Model Chance`,
   - `Market Chance`,
   - `Difference`,
   - `Recommendation`,
   - `Result`.
3. Keep advanced fields available under an expander or optional advanced table:
   - raw `model_probability`,
   - raw `market_probability`,
   - raw `edge`,
   - model version,
   - build id.
4. Use badges or clear text for:
   - PLAY,
   - LEAN,
   - PASS,
   - WAITING FOR STARTERS,
   - NO ODDS,
   - FINAL — WON,
   - FINAL — LOST,
   - NO PLAY.
5. Do not label PASS rows as wins/losses.
6. Add short explanatory copy:
   - “Pick means the side the model prefers relative to the market price.”
   - “This is not a staking policy.”

## Critical correctness constraints

- Do not alter the underlying prediction records.
- Displayed picked-team probabilities must correctly flip home probabilities
  when the picked side is away.
- Do not imply a sportsbook recommendation or guaranteed profit.

## Acceptance criteria

- A user can identify the pick, result, and reason without reading raw stats.
- Current advanced users can still inspect raw probability/edge values.
- Existing board tests pass.
- New tests cover away-side probability display and PASS/no-play labels.

## Required tests

- unit tests for picked-team probability flipping
- unit tests for result labels
- regression test for PASS row display

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- known limitations,
- whether manual Streamlit smoke was performed.
