# Current Project State

## Milestone

V1 data ingestion.

## Completed

- High-level MLB predictor architecture defined.
- V1 model candidates chosen:
  - Logistic Regression
  - Random Forest
  - XGBoost
- Validation strategies chosen:
  - expanding window,
  - rolling 2-season window,
  - rolling 3-season window,
  - untouched 2026 final holdout.
- Core rule established: all predictive features must be point-in-time safe.
- Vendor-neutral agent workflow defined:
  - Orchestrator,
  - Implementer,
  - Reviewer,
  - Tester.
- META-001 — repository/agent foundation completed.
- DATA-001 — local DuckDB/Parquet storage foundation completed.
- DATA-002 — immutable, idempotent MLB schedule ingestion completed.
- DATA-003 — timestamped, append-only moneyline odds ingestion completed.

## In progress

- None.

## In review

- DATA-004 — Silver normalization candidate after team-identity mapping repair.

## Blocked

- None.

## Current architecture decisions

- Python is the V1 implementation language.
- DuckDB + Parquet are the V1 storage layer.
- Raw source data is immutable.
- Bronze / Silver / Gold data layers are used.
- MLB `game_pk` is the canonical baseball game identifier.
- Odds data is stored as timestamped snapshots.
- Streamlit is the default lightweight V1 UI.
- The model target is binary home-team win probability.
- Model quality is judged primarily by probability quality, not raw accuracy.
- Betting-style ROI is secondary evaluation.
- Fliff is not part of the core system.

## Next implementation task

- DATA-004 is in re-review/testing after mapping-safety repair. FEAT-* unlock after gates pass and merge.

## Deferred follow-ups

- Complete missing execution-contract metadata in downstream task files before each becomes executable; DATA-004 metadata was completed at dispatch.
- Clarify the non-authoritative archival status of `old/docs/codex_workflow.md` in a separately authorized task.

## Notes for the next harness

Do not assume conversational history is available.

Read:

- `AGENTS.md`
- `docs/project_execution_contract.md`
- `docs/roadmap.md`
- the assigned task

before implementation.
