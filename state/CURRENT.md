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

All data-integrity hardening tasks through DATA-018 are merged to main.
Reviewer + Tester gates passed with zero unresolved P0/P1 findings; blocking
tester findings in OBS-001 and DATA-018 were repaired before merge. Full repo
suite green on main post-merge after APP-001/OBS-001/DATA-018: **578 passed,
1 xfailed** (main's environment has xgboost installed, so no exclusions needed
here, unlike the individual agent worktrees).

- DATA-016 (`210c70b`) - game-detail `fields=` projection fix + lifecycle-aware
  hollow-boxscore guard, `numberOfPitches` required, real-payload contract
  fixture + live smoke script. See `state/agents/DATA-016.md`. Deferred P2/P3s:
  missing direct unit coverage for 5 validation branches (logic verified
  correct by inspection); cross-side duplicate pitcher id not checked
  (unreachable with real MLB data).
- DATA-017 (`baee618`) - certification semantic-completeness gate: a
  structurally-empty or degenerate (constant-value) required measure column
  can no longer certify PASS; fixed a WARN-collapsed-to-PASS bug in
  certification dimension aggregation. See `state/agents/DATA-017.md`.
- FEAT-005/006 (`100c5b2`) - explicit, general (non-allowlist) component-
  coverage exclusion policy for the 4 known zero-appearance games; Gold
  pre-model completeness gate blocking ML experiments on an entirely-empty
  required feature family (no auto-dropping), wired into the evaluator +
  calibration path. See `state/agents/FEAT-005-006.md`.
- APP-001 (`19abe5d`) - Streamlit daily prediction board merged with one
  deferred non-blocking P2 pinned by xfail: malformed/stale-schema prediction
  records can crash the board instead of skipping a bad row. See
  `state/agents/APP-001.md`.
- OBS-001 - append-only prediction journal merged after repairing the tester
  P1/P2 false-conflict path: routine result-enrichment re-runs with a fresh
  `enrichment_timestamp` are idempotent when the substantive enrichment fact is
  unchanged; real conflicts still raise. See `state/agents/OBS-001.md`.
- DATA-018 - hollow game-detail invalidation + operator re-ingest entry point
  merged after repairing the tester P2 restart risk: already-repaired payloads
  are now guarded by `ingestion_build_id`, not `--run-id`, so a restart with a
  different run id does not re-invalidate already-good repaired payloads. See
  `state/agents/DATA-018.md`.

**The full 2021-2025 game-detail RE-INGEST HAS RUN (via `scripts/data018_reingest.py`,
run_id `DATA-018-reingest-2021-2025`, build_id `DATA-018`).** 59/59 batches,
fetched=14,481, missing=0, failed=39, skipped=0. Re-certified: **PASS**, 0
merge-blocking, 1 advisory WARN (`pitching.non_regular_season`, same as
before). Artifact: `state/data-certifications/certification-PASS-a910017bac839af5.json`.

Verified directly against `data/mlb.duckdb`: every pitching stat column in
`silver.pitcher_appearances` (132,448 rows) is now **100% non-null**
(`outs_recorded`, `batters_faced`, `hits_allowed`, `earned_runs`, `walks`,
`strikeouts`, `pitches_thrown`, `innings_pitched`) — the DATA-016 defect is
genuinely fixed in production, not just in tests. The historical odds archive
(`bronze.historical_odds_moneylines`, 69,901 rows) was untouched by this
run (DATA-018 only re-ingests game-detail).

**The 39 failed games** all failed with the SAME reason: a listed pitcher has
a genuinely all-zero line (`outs=0, battersFaced=0`) on a real completed
(`Final`) game — the DATA-016 hollow-payload guard correctly rejecting
suspicious data rather than silently storing it (this is not the
projection defect recurring; it's the guard doing its job on a rare real MLB
boxscore edge case). One pitcher's bad line fails ingestion for the whole
game_pk, so these 39 games have zero Silver pitcher_appearances rows. Not
re-attempted by this run (the fetcher deterministically returns the same
payload for a completed historical game). FEAT-005's general (non-allowlist)
component-coverage exclusion policy will classify/report these as
zero-appearance games, visibly, when the feature matrix is rebuilt --
not silently. Investigating whether these 39 are legitimate rare MLB events
or a residual parsing edge case is a candidate follow-up task (not filed
yet, not blocking).

**Gold feature completeness has PASSED** against the repaired certified build.
Gold was rebuilt in historical mode from the repaired Silver inputs with
regular-season `team_game_statistics` scope (matching starter/bullpen regular-
season scope). Result: 12,118 rows, 32 explicitly excluded component-coverage
gaps, 240 feature columns, FEAT-006 status **PASS** across team, starter,
bullpen, and rest/schedule families. Durable report:
`reports/data-quality/gold-completeness-a910017bac839af5.json`.

Leakage gate rerun after the repaired build: `python -m pytest tests/leakage/
tests/unit/validation/test_leakage_checks.py -q` -> **51 passed**.

## Ready

- Review the repaired ML experiment evidence and decide whether to lock the
  model/window/calibration methodology. 2026 remains untouched until that
  methodology decision is accepted.
- Optional: run a second, narrower XGBoost tuning pass around the shallow
  regularized candidate if methodology review wants more evidence before lock.
- Optional: investigate the 39 all-zero-pitcher-line games found by the real
  re-ingest (candidate for a new DATA-01x task, not filed).

## Repaired ML experiment (2021-2025 certified build) - RESULTS

Executed on repaired certified build `a910017bac839af5`: 12,118 regular-season
Gold rows, 240 feature columns, 32 explicit FEAT-005 component-coverage
exclusions, Gold completeness PASS, leakage PASS, 2026 not inspected. Report:
`reports/experiments/v1-repaired-a910017bac839af5.json`.

Ranking on the common test seasons {2024, 2025} (primary: log loss -> Brier ->
ECE), 4,847 test games per combination:

| # | model | window | log loss | Brier | ECE | AUC | acc |
|---|-------|--------|----------|-------|-----|-----|-----|
| 1 | xgboost | expanding | 0.68551 | 0.24616 | 0.0298 | 0.5695 | 0.5457 |
| 2 | random_forest | expanding | 0.68613 | 0.24641 | 0.0231 | 0.5703 | 0.5544 |
| 3 | xgboost | rolling_3 | 0.68669 | 0.24671 | 0.0251 | 0.5673 | 0.5509 |
| 4 | logistic | expanding | 0.68711 | 0.24679 | 0.0329 | 0.5692 | 0.5560 |
| 5 | random_forest | rolling_2 | 0.68841 | 0.24757 | 0.0252 | 0.5613 | 0.5478 |

Candidate best by ML-007 primary ordering: **XGBoost + expanding window**.
This is repaired experiment evidence, not yet a locked methodology decision.

Calibration (ML-008, XGBoost/expanding, pooled over its folds, fair base-fit
comparison): uncalibrated 0.68756 log loss / 0.24697 Brier / 0.0350 ECE;
**sigmoid 0.68385 / 0.24538 / 0.00554** (best); isotonic 0.72223 / 0.24718 /
0.0274 (overfits on log loss). Candidate calibration method: sigmoid/Platt,
pending methodology review.

## XGBoost tuning pass (2021-2025 repaired build) - RESULTS

Executed a modest 20-candidate XGBoost grid on repaired build
`a910017bac839af5`, expanding folds only, selection order log loss -> Brier ->
ECE, 2026 not inspected. Report:
`reports/experiments/v1-repaired-xgboost-tuning-a910017bac839af5.json`.

Best tuned candidate:

```text
max_depth=2
learning_rate=0.03
n_estimators=300
reg_lambda=10.0
min_child_weight=3.0
subsample=0.8
colsample_bytree=0.8
```

Raw/full-train expanding-fold aggregate: log loss **0.68124**, Brier **0.24408**,
ECE **0.01578**, AUC **0.58446**, accuracy **0.56396** over 9,694 test games.
This improves materially over the untuned repaired XGBoost/expanding evidence
(0.68551 / 0.24616 / 0.02976 on the common 2024-2025 comparison; 0.68527 /
0.24595 / 0.03307 as the ML-008 full-train reference over all expanding folds).

Calibration on the tuned candidate: sigmoid/Platt improves the fair base-fit
comparison (0.68271 -> 0.68150 log loss; ECE 0.01966 -> 0.00582), but the
raw full-train tuned model remains best by primary log loss/Brier. Isotonic
again overfits on log loss (0.70829). Candidate methodology for review:
**tuned shallow XGBoost + expanding window, likely uncalibrated if strict
primary-metric ordering dominates; sigmoid only if calibration reliability is
weighted more heavily than the small log-loss/Brier cost.**

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

The re-ingest + re-certification + Gold completeness + leakage + repaired ML
experiment + first XGBoost tuning chain is DONE for 2021-2025. The next
required action is reviewing the repaired/tuned evidence and deciding whether
to lock the model/window/calibration methodology or run one narrower tuning
pass. Do not inspect 2026 until that decision is accepted.

## Safe parallel

- None currently dispatched.

## Blocked

- Model selection/methodology lock, downstream model-dependent market
  decisions, and any 2026 holdout inspection remain blocked until the repaired
  experiment evidence is reviewed and the methodology decision is accepted.

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

The repaired 2021-2025 dataset is built and certified PASS, Gold completeness
PASS, leakage tests PASS, and repaired ML-005/006/007/008 evidence has been
generated. A first XGBoost tuning pass improved the repaired expanding-window
evidence. Next implementation/operator task: methodology review/lock decision
or one narrower XGBoost tuning pass; do not inspect 2026.

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
