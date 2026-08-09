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
- DATA-011 - real-build certification runner: MLB-StatsAPI (toddrob99) fetcher
  adapters (`src/ingestion/mlb/statsapi_fetchers.py`) + `src/pipelines/certify_historical.py`
  sequencing the full build->certify flow. Completed (merged). The runner is
  in-repo and gated; the actual multi-hour 2021-2025 live pull is operator-run.
- DATA-013 - reconcile repeated `game_pk` in season schedule responses
  (postponed+rescheduled Final); conflicts FAIL. Completed (merged). Found via the
  DATA-011 real build.
- DATA-014 - reconcile suspended/resumed same-Final duplicate `game_pk` by
  outcome fields; genuine outcome conflicts FAIL. Completed (merged). Found via
  the DATA-011 real build.
- DATA-015 - `results.home_win_derivation` scoped to regular-season games so
  certification does not FAIL on legitimate spring-training ties; regular-season
  strictness preserved. Completed (merged). Found via the DATA-011 full build.
- **Real 2021-2025 certified dataset PRODUCED (DATA-011 executed).** Full live
  build ingested 14,520 games, 132,848 pitcher appearances, 29,015 starters, and
  69,901 archive moneylines (12,367 MATCHED to `game_pk`, 0 AMBIGUOUS).
  Certification: **PASS** (0 merge-blocking; 1 advisory WARN
  `pitching.non_regular_season`). Durable artifact committed at
  `state/data-certifications/certification-PASS-7225f7f46a5e27e9.json`
  (git_commit aee9435). This is the gate that unblocks FEAT-002/FEAT-003.
- DATA-012 - fix `results.valid_scores` so postponed/suspended/cancelled games
  that MLB reports with abstractGameState='Final' are not flagged (found via the
  DATA-011 real smoke test; game_pk 747139 on 2024-04-10). Completed (merged).

## Real-path validation (smoke)

The full live pipeline was smoke-tested end-to-end on a single real day
(2024-04-10) using the MLB-StatsAPI wrapper + the real 80MB odds archive:
14/14 game feeds fetched, Silver built, archive ingested (69,901 moneylines,
SHA-256 verified), all 14 games mapped MATCHED, and \u2014 after DATA-012 \u2014
certification returned PASS with no merge-blocking findings. The systematic
postponed-game blocker is resolved; the operator's full 2021-2025 run is expected
to certify cleanly.
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

- FEAT-002 - point-in-time starter features. Unblocked by the PASS certification.
  Owns `src/features/starter.py`. Must be point-in-time safe (shift-before-roll;
  never read a pitcher's current-game line as a pregame input, per ADR-002).
- FEAT-003 - point-in-time bullpen features. Unblocked by the PASS certification.
  Owns `src/features/bullpen.py`. Point-in-time safe; parallel with FEAT-002.

## Next required action

The 2021-2025 historical MLB dataset is certified PASS
(`state/data-certifications/certification-PASS-7225f7f46a5e27e9.json`). Dispatch
FEAT-002 (starter features) and FEAT-003 (bullpen features) in parallel; they own
separate feature modules. FEAT-004 (feature matrix) is the later aggregation
point. Feature builders must exclude non-regular-season pitcher data (advisory
`pitching.non_regular_season`) and be point-in-time safe.

## Safe parallel

- FEAT-002 and FEAT-003 own separate feature modules and may run in parallel.

## Blocked

- FEAT-004 and all ML work - wait for FEAT-002/FEAT-003 (FEAT-001 is done).
- MARKET-001 - waits for ML-008 (DATA-009 opening-market inputs are ready).

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

The real 2021-2025 dataset is built and certified PASS (DATA-011 executed;
DATA-013/014/015 fixed the real-data edges it surfaced). FEAT-002 (starter) and
FEAT-003 (bullpen) are ready and may be dispatched in parallel; FEAT-004 is the
later aggregation point.

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
