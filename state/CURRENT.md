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
- DATA-004 — normalized Silver datasets and MLB/odds mapping contract completed.
- FEAT-001 — point-in-time team strength / recent-form features completed.

## In progress

- None.

## In review

- None.

## Blocked

- FEAT-002 / FEAT-003 — `silver.pitcher_appearances` is an empty contract until appearance-capable ingestion exists.

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

- No parallel FEAT work is ready: FEAT-002/003 remain blocked on empty pitcher-appearance Silver data. Next unblock is appearance-capable ingestion (or an explicit interim ADR/task), then FEAT-002/003 in parallel, then FEAT-004.

## Deferred follow-ups

- Complete missing execution-contract metadata in FEAT-002/003 before they become executable.
- Add appearance-capable ingestion (or populate `pitcher_appearances` from richer boxscore/feed data) before FEAT-002/003.
- Optional P2: document/backfill legacy bronze odds rows with NULL team names; optional team-name alias table.
- Clarify the non-authoritative archival status of `old/docs/codex_workflow.md` in a separately authorized task.

## Notes for the next harness

Do not assume conversational history is available.

Read:

- `AGENTS.md`
- `docs/project_execution_contract.md`
- `docs/roadmap.md`
- the assigned task

before implementation.
