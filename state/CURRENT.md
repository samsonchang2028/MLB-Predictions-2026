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
- FEAT-002 - point-in-time starting-pitcher features (`src/features/starter.py`):
  shift-before-roll, current appearance excluded, explicit first-start/missing/
  changed-starter handling, leakage-tested. Completed (merged).
- FEAT-003 - point-in-time bullpen features (`src/features/bullpen.py`): recent
  ERA/WHIP + workload over prior 1/3 days, same-day doubleheaders ordered
  chronologically, leakage-tested. Completed (merged).
- FEAT-004 - game feature matrix (`src/features/build.py`): one row per game_pk
  from team/starter/bullpen features (home/away + differentials), target
  (home_win) isolated from features, prediction timestamp + certified build
  identity retained, uniqueness/cardinality enforced. Completed (merged).
- ML-001/002/003 - probabilistic P(home_win) model families completed (merged):
  logistic regression (`src/models/logistic.py`), random forest
  (`src/models/random_forest.py`), XGBoost (`src/models/xgboost_model.py`). All
  expose the shared `build_model`/`predict_proba`/`model_metadata` contract so the
  ML-004 walk-forward evaluator can drive them uniformly. scikit-learn + xgboost
  added as dependencies.
- ML-004 - walk-forward validation framework (`src/evaluation/splits.py` +
  `runner.py`): deterministic expanding + rolling 2/3-season folds (no train/test
  overlap, chronological, 2026 excluded), stable feature vectorization of the
  FEAT-004 matrix, per-fold fresh-model fit (preprocessing inside the fold), and
  probability-quality metrics (log loss/Brier/calibration; ROC-AUC/accuracy
  secondary) across all three families. Completed (merged); leakage-tested.
- ML-004A - game_pk-keyed per-fold predictions in the runner
  (`run_evaluation(..., return_predictions=True)`): opt-in per-fold prediction
  table [{game_pk, p_home_win, y_true}] for labeled test rows, aligned after the
  unlabeled-drop backing the metrics; default return shape unchanged. Single
  source of truth for experiment predictions. Completed (merged).
- ML-005/006 - development window experiments completed (merged): expanding
  (`src/experiments/expanding.py`, 4 ADR-003 folds) and rolling 2/3-season
  (`src/experiments/rolling.py`), each driving all three families via the runner
  and emitting an identical result schema (fold_metrics + game_pk-keyed
  predictions) for ML-007. 2026 never inspected.
- ML-007 - model family x training-window comparison
  (`src/experiments/comparison.py`): pools run_expanding + run_rolling predictions
  per (model, window), ranks by primary metrics (log loss -> Brier -> calibration;
  ROC-AUC/accuracy secondary), and selects the winner on the common test seasons
  {2024, 2025} for a fair head-to-head. 2026 never used for selection (asserted).
  Completed (merged).
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

- ML-008 - probability calibration. Unblocked: ML-007 merged (model x window
  comparison selects the winner by primary metrics on the common test seasons
  {2024, 2025}). Fit/select the calibration method on development folds only,
  refit calibrators inside the appropriate training partition, and keep 2026
  untouched (ADR-003: calibration-method selection excludes 2026).

## Next required action

Dispatch ML-008 (probability calibration) as the single node consuming the
ML-007 comparison / the walk-forward predictions. It then unblocks MARKET-001.

## Safe parallel

- None right now (ML-008 is a single node).

## Blocked

- MARKET-001 - waits for ML-008 (DATA-009 opening-market inputs are ready).
- PIPE-001 / APP / OBS - downstream of the model + market layer.

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
- FEAT-004 follow-ups (non-blocking, for ML/PIPE consumers): (a) P2 \u2014 diff_*
  column eligibility is based on observed non-None values across the build, so a
  small cold-start inference build could omit diff_ columns present in the
  training matrix (schema drift). Base diff eligibility on a declared column
  union and emit None when unavailable; address in FEAT-004 hardening or PIPE-001.
  (b) P3 \u2014 prediction_timestamp = game_date; document it as a pregame calendar
  reference or derive a strict pre-first-pitch cutoff (ADR-002). (c) P3 \u2014
  certification_status is hardcoded "PASS" (safe: the gate raises otherwise);
  could echo the artifact status.
- FEAT-002/FEAT-003 P3 design choices (non-blocking, for FEAT-004/model review):
  (a) FEAT-002 rolling windows and days_rest span the offseason (cross-season
  continuity) rather than resetting per season — documented; confirm during
  modeling whether a season reset is preferred; (b) FEAT-003 day-based workload
  windows use 24h*N timestamp deltas rather than calendar-day boundaries —
  documented, does not cause leakage.
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
