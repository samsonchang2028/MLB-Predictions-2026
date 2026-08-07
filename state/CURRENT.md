# Current Project State

## Milestone

V1 historical data completion and certification planning.

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
- META-001 - repository/agent foundation completed.
- DATA-001 - local DuckDB/Parquet storage foundation completed.
- DATA-002 - immutable, idempotent MLB schedule ingestion completed.
- DATA-003 - timestamped, append-only live moneyline odds ingestion completed.
- DATA-004 - normalized Silver datasets and MLB/live-odds mapping contract completed.
- FEAT-001 - point-in-time team strength / recent-form features completed.
- DATA-008 - immutable, checksum-verified historical odds archive ingestion completed (merged 479af32).
- DATA-005 - immutable, restartable MLB game-detail backfill and Silver pitcher appearances completed (merged e50747c).
- DATA-006 - historical MLB data validation package (`src/validation/`) + side-effect-free certification runner completed (merged 1d9b83b).
- DATA-010 - MLB game-detail backfill restart resilience (reused-run_id upsert + per-game integrity isolation) completed (merged a87ef2b).
- DATA-007 - historical MLB data certification gate: versioned PASS/FAIL artifact layer (`src/validation/certification.py`, `state/data-certifications/`) consuming the DATA-006 runner. Completed (merged).
- DATA-009 - historical odds archive validation + auditable odds->`game_pk` mapping (MATCHED/UNMATCHED/AMBIGUOUS) with season/date/sportsbook coverage report (`src/validation/odds_mapping.py`). Completed (merged).
- ADR-004 accepted:
  - MLB Stats API remains the historical baseball source,
  - 2021-2025 are the V1 historical development seasons,
  - the ArnavSaraogi `mlb_odds_dataset.json` archive is the V1 historical odds source,
  - opening moneyline odds are the canonical historical market benchmark,
  - live timestamped odds and historical opening-odds benchmarking are separate methodologies,
  - a formal historical MLB data certification gate is required before dependent feature/model work.

## In progress

- None.

## In review

- None.

## Ready

- None as pure code tasks. All validation/certification/odds tooling for the V1
  historical gate is now merged (DATA-006, DATA-007, DATA-009) alongside the
  ingestion resilience fix (DATA-010).

## Next required action (gating the whole downstream graph)

The certification LAYER exists, but no real 2021-2025 certified dataset has been
produced. FEAT-002 / FEAT-003 (and everything downstream) require a PASS
certification artifact from an actual build, not just the tooling. The next step
is an operational/data build task:

1. Run the real MLB game-detail backfill (DATA-005 machinery) + Silver
   normalization over 2021-2025.
2. Run DATA-008 real historical odds archive ingestion (verify published
   SHA-256) and DATA-009 mapping/coverage over it.
3. Run `validation.certify_and_write` to emit a durable
   `state/data-certifications/certification-<STATUS>-*.json`.
4. Only if the artifact is PASS (no P0/P1 or leakage failures) do FEAT-002 /
   FEAT-003 become ready.

Recommend the Orchestrator open a new task (e.g. DATA-011) to execute and commit
this real-build certification, since it involves live API/data pulls rather than
code changes. Flagged to the user for direction (network/data access needed).

## Safe parallel

- None pending (no ready code tasks). FEAT-002 and FEAT-003 will be parallel
  once a PASS certification artifact from a real build exists.

## Blocked

- FEAT-002 / FEAT-003 - blocked on a PASS certification artifact from a real
  2021-2025 build (DATA-007 layer is merged; the real certified build is not yet
  produced).
- FEAT-004 and all ML work - wait for FEAT-002/FEAT-003 plus feature deps.
- MARKET-001 - waits for ML-008 (DATA-009 odds benchmark inputs are now ready).

## Current architecture decisions

- Python is the V1 implementation language.
- DuckDB + Parquet are the V1 storage layer.
- Raw source data is immutable.
- Bronze / Silver / Gold data layers are used.
- MLB `game_pk` is the canonical baseball game identifier.
- Odds data for live/future predictions is stored as timestamped snapshots.
- Historical odds archive benchmarking uses opening moneyline odds and must be labeled as model edge versus opening market.
- Historical ROI from the archive is simulated ROI at opening prices and is secondary to log loss, Brier score, and calibration.
- Streamlit is the default lightweight V1 UI.
- The model target is binary home-team win probability.
- Model quality is judged primarily by probability quality, not raw accuracy.
- Betting-style ROI is secondary evaluation.
- Fliff is not part of the core system.

## Next implementation task

No pure-code task is ready. DATA-006, DATA-007, DATA-009, DATA-010 are merged.
The pipeline is now gated on producing and committing a PASS certification
artifact from a REAL 2021-2025 dataset build (see "Next required action"). This
needs live MLB/odds data pulls, so it should be a new explicitly-scoped task
(proposed DATA-011) and requires user direction on data/network access.

## Deferred follow-ups

- DATA-005 reviewer P3s: (a) reused fixed `run_id` PK violation — RESOLVED by
  DATA-010 (attempt upsert on `(ingestion_run_id, game_pk)`); (b) one
  tampered/missing raw file aborting the whole backfill — RESOLVED by DATA-010
  (per-`game_pk` isolation, retryable failed attempt); (c) spring/exhibition
  (`gameType` S/E) appearances — now surfaced by DATA-006
  `pitching.non_regular_season` (advisory WARN); FEAT-002/003 must still exclude
  non-regular-season pitcher data before feature use; (d) rollback of recreated
  Silver pitcher tables on a normalize failure — still open; consider covering
  in DATA-007 certification tests.
- DATA-006 P3s (non-blocking, deferred): (1) `check_processing_determinism`
  copies the whole origin DB into memory per certification via
  `COPY FROM DATABASE` — flag as a scaling caveat for DATA-007 running on the
  full 2021-2025 dataset (consider copying only the 3 bronze tables normalize
  reads); (2) scratch `DETACH` is best-effort try/except.
- DATA-010 P3s (non-blocking, deferred): (1) `bronze.mlb_game_detail_status`
  view reports `fetched` for a game whose raw archive is tampered (payloads row
  present) while the same row surfaces the mismatch `error_message` — internally
  contradictory but not silent; on-disk tamper detection is owned by DATA-006
  `bronze.detail_payload_integrity`; (2) a tampered raw file is recorded as
  `failed` but the existing payloads row short-circuits re-fetch, so recovery
  needs manual intervention rather than automatic retry — "retryable" wording is
  optimistic.
- FEAT-001 is complete, but its downstream use against real 2021-2025 data must
  be covered by DATA-006/DATA-007 validation and certification.
- Optional P2: document/backfill legacy bronze odds rows with NULL team names;
  optional team-name alias table.
- Clarify the non-authoritative archival status of `old/docs/codex_workflow.md`
  in a separately authorized task.

## Notes for the next harness

Do not assume conversational history is available.

Read:

- `AGENTS.md`
- `docs/project_execution_contract.md`
- `docs/roadmap.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- the assigned task

before implementation.
