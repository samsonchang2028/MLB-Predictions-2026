# OBS-002 — daily prediction result enrichment operator

## Status

candidate (implemented; awaiting review/tester)

## Dependencies

- OBS-001
- PIPE-003
- PIPE-005

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Add a production operator that refreshes completed MLB game results and appends
immutable prediction-result enrichment rows to `state/predictions/journal.jsonl`.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/OBS-001-prediction-journal.md`
- `tasks/PIPE-003-live-operator-hardening.md`
- `tasks/PIPE-005-pregame-detail-refresh.md`
- `docs/decisions/ADR-002-pregame-leakage.md`

## Allowed files

- `scripts/enrich_prediction_results.py`
- `src/observability/`
- `tests/unit/observability/`
- `tests/integration/observability/`
- `tests/unit/scripts/`
- `tasks/OBS-002-result-enrichment-operator.md`
- `tasks/index.md`
- `state/CURRENT.md`

## May modify if necessary

- `src/app/performance.py`
- `src/app/performance_page.py`
- `src/app/board.py`
- `src/app/daily_board_page.py`
- `README.md`

## Do not modify

- model methodology / ADR-006
- historical training/evaluation code
- 2026 holdout evaluation artifacts except reading local completed-game rows for
  daily prediction journaling

## Inputs

- immutable prediction records: `state/predictions/daily.jsonl`
- existing OBS-001 journal append-only API
- refreshed MLB schedule/Silver result rows:
  `silver.team_game_statistics.score` and `is_winner`

## Outputs

- append-only enrichment records: `state/predictions/journal.jsonl`
- explicit operator logs: predictions read, final results available, records
  written, skipped/no-result rows

## Requirements

1. Add a script-level operator that can be run after games finish:

   ```powershell
   python.exe scripts\enrich_prediction_results.py --date YYYY-MM-DD
   ```

2. Before reading results, refresh the MLB schedule for the target date.
   The production operator may read completed score/winner values directly from
   the refreshed Bronze schedule JSON to avoid running full Silver normalization
   for a small post-game enrichment pass.
3. Use `observability.journal.attach_results`; do not mutate
   `daily.jsonl`.
4. Only enrich games with unambiguous completed results. Games without final
   scores/winners must be skipped with explicit reasons, not treated as losses
   or dropped silently.
5. Re-runs must be idempotent. Re-observing the same result with a later
   enrichment timestamp must not raise a conflict.
6. Preserve chronology: result enrichment is post-game observability only and
   must not feed back into model features, model selection, or same-game
   prediction inputs.
7. The performance dashboard should keep reading the journal as an already
   computed artifact. It may improve empty-state copy from "Run OBS-001" to the
   concrete OBS-002 operator command.

## Critical correctness constraints

- Original prediction rows are immutable.
- A result row is valid only when the game has enough completed-game evidence to
  derive `actual_home_win`.
- No market ROI or staking result should be computed here; that belongs to
  MARKET-002.
- No prediction should be created or changed by this operator.

## Acceptance criteria

- Running the operator after at least one game finishes writes enrichment rows
  for finished predicted games and skips unfinished games.
- Re-running the operator over the same date writes zero new rows for already
  journaled results.
- Streamlit performance page no longer says "Run OBS-001 enrichment first" for
  the missing journal case; it points to the OBS-002 operator.
- Relevant unit/integration tests pass.

## Required tests

- unit test for loading completed result rows from Silver shape
- unit or script test that unfinished games are skipped, not failed
- regression test for idempotent re-run with a fresh enrichment timestamp
- integration-style test using JSONL prediction/journal stores

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- known limitations,
- whether live MLB result refresh was smoke-tested.

## Implementation handoff

- Added `scripts/enrich_prediction_results.py` operator for post-game enrichment.
- The operator optionally refreshes the target day MLB schedule, reads completed regular-season results from refreshed Bronze schedule JSON or existing Silver fixtures, and calls OBS-001 `attach_results`.
- Added journal score fields (`home_score`, `away_score`) so artifact-backed dashboards can display final scores without mutating `daily.jsonl`.
- Daily board can optionally join `state/predictions/journal.jsonl` and display pending/final/correctness status.
- Focused tests added for completed-result loading, separate journal writes, schedule-refresh wiring, score fields, and board join behavior.
- Focused unit/integration tests and live MLB smoke passed. Status remains candidate until independent review/tester gate passes.
