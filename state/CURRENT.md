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
- ML-008 - probability calibration (`src/evaluation/calibration.py`): Platt/
  sigmoid + isotonic fitted on a chronological INNER calibration partition of each
  fold's training rows (base-fit earlier, calibration later, both disjoint from
  the test fold), comparing calibrated vs uncalibrated log loss / Brier /
  reliability on identical folds with the same base-fit partition. 2026 untouched.
  Completed (merged); leakage-tested (a leaky base-fit provably fails the invariant).
- MARKET-001 - market probability and edge engine (`src/market/`): American odds
  -> implied probability, two-way no-vig normalization (sums to 1.0) with
  overround, model-vs-market edge, expected value. Preserves bookmaker identity +
  odds snapshot timestamp and REFUSES snapshots not strictly before the prediction
  cutoff/first pitch (ADR-002); CLOSING odds usable only as post-hoc benchmarks;
  archive opening odds labeled model-edge-vs-opening-market / simulated ROI at
  opening prices; UNMATCHED/AMBIGUOUS DATA-009 records excluded from canonical
  evaluation. Completed (merged); formulas pinned to hand-verified known values.
- PIPE-001 - daily prediction pipeline (`src/pipelines/daily.py`): one
  deterministic run with injected providers (no forced network/DB) producing
  IMMUTABLE append-only prediction records carrying game_pk, prediction_timestamp,
  model_version, build_id (feature/schema version), model_probability,
  odds_snapshot_timestamp, no-vig market_probability and edge. Enforces
  prediction_timestamp < first pitch and snapshot < cutoff (ADR-002), skipping
  violators with an explicit reason; re-runs are idempotent and a conflicting
  re-write raises. CLOSES the FEAT-004 P2 gap: inference features come from the
  declared training column union (missing -> NaN in order; unexpected column
  raises schema drift). Completed (merged).
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

## Recently merged

- DATA-016 - game-detail `fields=` projection fix + lifecycle-aware hollow-boxscore
  guard MERGED to main (`210c70b`, no-ff merge of `agent/DATA-016-pitching-stats`).
  Reviewer (APPROVE) and Tester (LOOKS_SAFE_TO_MERGE) gates both passed with no
  P0/P1; full repo suite green on main post-merge (447 passed). See
  `state/agents/DATA-016.md` for the full re-gate record and deferred P2/P3s
  (missing direct unit coverage for 5 validation branches — logic verified
  correct by inspection; cross-side duplicate pitcher id not checked —
  unreachable with real MLB data). **The full 2021-2025 game-detail RE-INGEST
  (~4.5h, single-writer) has NOT been launched** — the fix only applies to
  future fetches; already-stored Bronze rows are still the pre-fix hollow data.
  This re-ingest + re-certification is the next required action before FEAT-002/
  FEAT-003 pitching features or a re-run experiment can be trusted.

## Ready

- DATA-017 - certification must FAIL on 100%-NULL declared measure columns; this
  gap let the hollow build certify PASS. Candidate complete on
  `agent/DATA-017-column-coverage`; Reviewer (APPROVE) and Tester
  (LOOKS_SAFE_TO_MERGE) gates both passed with no P0/P1 findings. Awaiting merge
  decision.
- FEAT-005 / FEAT-006 - component-coverage policy (4 regular-season games with
  zero parsed appearances, general observable-coverage rule not a game_pk
  allowlist) + Gold pre-model completeness gate (blocks ML experiments when a
  required feature family is entirely empty; no auto-dropping). Candidate
  complete on `agent/FEAT-005-006-gold-completeness`; Reviewer (APPROVE) and
  Tester (LOOKS_SAFE_TO_MERGE) gates both passed with no P0/P1 findings.
  Awaiting merge decision.
- OBS-001 + APP-001 - prediction journal and Streamlit board (parallel-safe).
  Dispatch was deferred to run the real experiment; both remain unblocked.

## First real experiment (2021-2025 certified build) - RESULTS

Executed on the certified build `7225f7f46a5e27e9`: 12,146 regular-season games,
211 feature columns, seasons balanced (~2,430/season), 100% labeled, home-win rate
0.5315. Report: `reports/experiments/v1-real.json`.

Ranking on the common test seasons {2024, 2025} (primary: log loss -> Brier ->
ECE), 4,857 test games per combination:

| # | model | window | log loss | Brier | ECE | AUC | acc |
|---|-------|--------|----------|-------|-----|-----|-----|
| 1 | logistic | expanding | 0.68395 | 0.24545 | 0.0169 | 0.5662 | 0.5516 |
| 2 | logistic | rolling_3 | 0.68496 | 0.24594 | 0.0180 | 0.5626 | 0.5495 |
| 3 | logistic | rolling_2 | 0.68615 | 0.24652 | 0.0245 | 0.5611 | 0.5450 |
| 4 | xgboost | expanding | 0.68732 | 0.24706 | 0.0252 | 0.5604 | 0.5448 |
| 7 | random_forest | rolling_3 | 0.69206 | 0.24932 | 0.0332 | 0.5476 | 0.5403 |

Best: **logistic regression + expanding window**. Reference: predicting the base
rate (0.5315) gives log loss ~0.6912 / Brier ~0.2490, so the model beats the base
rate by ~0.007 log loss - a real but WEAK edge. Expanding beat both rolling
windows for every family; the linear model beat both nonlinear families.

Calibration (ML-008, logistic/expanding, pooled over its folds):
uncalibrated 0.68897 / ECE 0.0287; **sigmoid 0.68506 / ECE 0.00965** (best);
isotonic 0.71753 / ECE 0.0414 (overfits). Platt/sigmoid calibration is the clear
choice and cuts reliability error ~3x.

**Interpretation caveat (important):** these numbers reflect TEAM FEATURES ONLY.
All starter and bullpen ERA/WHIP features were empty (DATA-016), so the pitching
signal - normally the strongest input to an MLB moneyline model - was absent. The
weak edge is expected under that handicap and should be re-measured after
DATA-016 re-ingest. No 2026 data was touched.

## Next required action

Dispatch DATA-016 (pitching-stat projection fix + re-ingest) and DATA-017
(certification coverage check) - DATA-017 can proceed in parallel since it touches
`src/validation/` while DATA-016 touches `src/ingestion/mlb/`. Then re-certify,
rebuild the matrix, and re-run the experiment to get trustworthy metrics.

## Safe parallel

- DATA-016 (`src/ingestion/mlb/`) and DATA-017 (`src/validation/`) own disjoint
  surfaces. FEAT-005 (`src/features/build.py`) is also disjoint. OBS-001 and
  APP-001 remain parallel-safe with each other.

## Blocked

- Nothing structurally. Trustworthy model metrics are gated on DATA-016.

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
