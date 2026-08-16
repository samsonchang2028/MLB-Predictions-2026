# ML-012 — Feature-family ablation study

## Status

implemented (candidate for review/test gates)

## Dependencies

- ML-009 (locked ADR-006 baseline to compare against)
- FEAT-006 (Gold completeness gate — matrix must PASS before use)

## Execution

Primary role: `implementer`

Review required: `yes` (leakage risk is explicitly in scope)

Tester required: `yes` (determinism + leakage tests for the new ablation harness)

Worktree required: `yes`

## Goal

Determine which Gold feature families (team, starter, bullpen, recent-form,
rest/schedule, home/away context, differential features — use the repo's
actual taxonomy in `src/features/build.py`'s `_COMPONENTS` and column-naming
convention, not an invented one) actually improve out-of-sample probability
quality for the locked XGBoost model, using the existing walk-forward
framework. Produce a research report with KEEP / INVESTIGATE / REMOVE
verdicts per family and a ranked list of the top 3 candidate future feature
additions. This is research/analysis — it does not change the production
model, its features, or ADR-006's locked selection.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-006*.md` (locked V1 methodology — the model config this
  study must not alter)
- `docs/decisions/ADR-002*.md` or equivalent point-in-time-correctness ADR
- `src/features/build.py` (`_COMPONENTS`, `feature_columns` construction —
  the real feature taxonomy)
- `src/evaluation/splits.py`, `src/evaluation/runner.py` (walk-forward
  framework — expanding + rolling 2/3-season folds)
- `src/experiments/expanding.py`, `rolling.py`, `comparison.py` (existing
  experiment pattern to follow, not reinvent)
- `src/evaluation/calibration.py` (train-only calibration partitioning)
- `src/models/xgboost_model.py`, `logistic.py`, `random_forest.py`
- `reports/experiments/v1-repaired-a910017bac839af5.json` and
  `reports/experiments/v1-model-diagnostics.json` (existing baseline
  numbers to compare against — do not re-derive from scratch what's already
  measured)

## Allowed files

- new module(s) under `src/experiments/` (e.g. `ablation.py`) — reuse
  `run_evaluation`/the existing runner rather than rebuilding fold logic
- new tests under `tests/unit/experiments/`
- new report artifact(s) under `reports/experiments/` (JSON, machine-
  readable, following the existing report shape) plus a markdown research
  report under `docs/research/` or `reports/experiments/`

## Do not modify

- `src/features/*.py` (feature definitions themselves — this task measures
  value, it does not change what's computed)
- `src/models/*.py`, ADR-006 locked hyperparameters/model selection
- `src/pipelines/`, `scripts/daily_predictions.py`, any Streamlit `src/app/`
  files
- anything under `src/ingestion/`, `src/market/`
- the final 2026 holdout: must remain untouched by this study (no feature,
  hyperparameter, model-family, calibration-method, or window selection may
  reference 2026)

## Inputs

- the existing certified Gold feature matrix (via `build_feature_matrix`,
  same certified build already used by ML-007/ML-008/ML-011)
- the locked XGBoost config (ADR-006) as the primary model under test;
  logistic/random-forest only as sanity checks, not a new comparison project

## Outputs

- `src/experiments/<ablation module>.py` — incremental and/or leave-one-
  group-out ablation harness built on the existing walk-forward runner
- `reports/experiments/ml-012-feature-ablation.json` — machine-readable
  metrics table (per family, per fold, per metric)
- a markdown research report (feature-family definitions, experiment setup,
  fold/window definitions, metrics table, calibration comparison, per-fold
  stability, KEEP/INVESTIGATE/REMOVE per family, leakage findings, top 3
  ranked next experiments)

## Requirements

- Group real Gold columns into feature families using the actual naming
  convention (`home_/away_/diff_{component}_{key}`), not a hand-invented
  grouping that doesn't match what's actually in the matrix.
- Incremental ablation (baseline → +team → +starter → +bullpen → ...) and/or
  leave-one-group-out (full model minus each family) — implementer's choice
  of which is more informative given the actual family count, document the
  choice.
- Primary metrics: log loss, Brier score, calibration (ECE or equivalent
  already used elsewhere in this repo). Secondary: ROC-AUC, accuracy,
  per-fold stability. Do not optimize for or report simulated ROI as a
  selection criterion.
- Preserve all three existing window strategies (expanding, rolling 2-season,
  rolling 3-season) at minimum for the primary model; logistic/RF sanity
  checks may use expanding only if that's sufficient to sanity-check the
  ranking.
- Redundancy/leakage review of the Gold matrix is required, not optional: look
  for suspiciously strong predictors, postgame-derived columns, target
  proxies, duplicate representations, unstable/missing coverage. **If
  anything looks like possible leakage, stop, do not continue interpreting
  ablation results as valid, and report it as a P0/P1 finding** — do not
  silently work around it.

## Critical correctness constraints

- No 2026 data used for feature/model/hyperparameter/calibration/window
  selection at any point (verify with an explicit assertion in the harness,
  same pattern `src/experiments/comparison.py` already uses).
- Train-only preprocessing (scaler/imputer/encoder/calibration fit only on
  the fold's training partition) — reuse the existing runner's guarantees,
  do not reimplement fold-splitting logic.
- Chronological folds only, no shuffling, no train/test overlap.
- Ablations must reuse the certified, already-completeness-gated (FEAT-006
  PASS) Gold build — do not silently drop or impute missing feature-family
  columns in a way that changes completeness semantics.

## Acceptance criteria

- A runnable ablation script/module producing the JSON report from a single
  command, following the existing experiment scripts' invocation pattern.
- The markdown report answers, per feature family: does it improve log loss?
  Brier? Is the improvement consistent across folds/seasons or concentrated
  in one? Does it help or hurt calibration? KEEP/INVESTIGATE/REMOVE verdict.
- Top 3 ranked candidate future feature additions (e.g. confirmed lineups,
  park factors, weather, handedness/matchup, bullpen availability, umpire
  data), ranked by likely predictive value, temporal-data availability,
  engineering effort, leakage risk, and maintainability — brief justification
  per item, not a full spec.
- No change to production model selection, ADR-006, or any file outside the
  allowed list.

## Required tests

- unit: ablation harness correctly assembles each feature-family subset
  (explicit column-membership assertions, not just "runs without error")
- leakage: an explicit test that mutates 2026-only or future-fold data and
  proves earlier-fold ablation results are unaffected (mirrors the existing
  leakage-test pattern in `tests/leakage/`)
- regression: harness output is deterministic given a fixed certified build
  (same input → same metrics, no random-seed drift across repeated runs)

## Handoff

Record: summary, files changed, feature families tested (with real column
lists), strongest/weakest contributors, any leakage concerns found and how
resolved/escalated, commands run, test results, known limitations, and the
top-3 recommended next experiments.

### Handoff (implementer, 2026-08-15)

**Summary.** Built `src/experiments/ablation.py`, a feature-family ablation
harness on top of the existing ML-004 walk-forward runner
(`evaluation.runner.run_evaluation`) and ML-004 fold definitions
(`evaluation.splits`) -- no new fold logic, no new metric math, no changed
feature computation. Ran it against the certified 2021-2025 build
(`a910017bac839af5`) via a new operator script
(`scripts/ml012_feature_ablation.py`, following `scripts/rerun_repaired_experiment.py`'s
loader pattern) and wrote the machine-readable report and markdown research
report. Full markdown report: `docs/research/ml-012-feature-ablation.md`.

**Files changed** (all new; nothing existing modified):
- `src/experiments/ablation.py` -- ablation harness (family taxonomy, subset
  assembly, leave-one-out + incremental drivers, 2026-holdout assertion,
  univariate leakage scan).
- `scripts/ml012_feature_ablation.py` -- operator entry point producing the
  JSON report from a single command. **Not in the task's explicit allowed-files
  list** (which names `src/experiments/`, `tests/unit/experiments/`,
  `reports/experiments/`, `docs/research/`), but required by the acceptance
  criterion "a runnable ablation script ... producing the JSON report from a
  single command, following the existing experiment scripts' invocation
  pattern" -- every other `src/experiments/*.py` module in this repo is driven
  by exactly this kind of `scripts/*.py` entry point
  (`rerun_repaired_experiment.py`, `tune_repaired_xgboost.py`), so an ablation
  module without one could not satisfy that criterion. `scripts/` is not on
  the do-not-modify list (only `scripts/daily_predictions.py` is named).
  Minimum necessary scope expansion per `agents/implementer.md`.
- `tests/unit/experiments/test_ablation.py` -- 12 tests (assembly-correctness,
  leakage, determinism; see below).
- `reports/experiments/ml-012-feature-ablation.json` -- generated report.
- `docs/research/ml-012-feature-ablation.md` -- markdown research report.

**Feature families tested** (real `features/completeness.REQUIRED_FAMILY_COLUMNS`
taxonomy, reused verbatim, not invented; full column lists in the JSON report's
`families` key and in the markdown report Section 2):
- `team` (84 columns): win%, runs scored/allowed, run differential, `before`/`L7`/`L14`/`L30`, home/away/diff.
- `starter` (75 columns): probable/actual starter identity + season and `L3`/`L5`/`L10` ERA/WHIP/K-rate/BB-rate/IP-per-start.
- `bullpen` (45 columns): team bullpen ERA/WHIP/IP/appearances over `L7`/`L14`/`L30`.
- `rest_schedule` (36 columns): starter `days_rest` + previous-start workload, bullpen usage in the prior 1/3 days.
- 84+75+45+36 = 240 = the certified build's exact feature-column count; zero unclassified columns (verified both against `reports/data-quality/gold-completeness-a910017bac839af5.json` and by the harness itself, which raises rather than silently drops any column outside this taxonomy).

**Strongest / weakest contributors** (ADR-006 locked XGBoost, pooled log loss,
all three windows; full table in the markdown report Section 5):
- Strongest, consistently: **team** (removal cost +0.00227 to +0.00319 log loss across the 3 windows), then **starter** (+0.00168 to +0.00245). Both monotonic across expanding/rolling_2/rolling_3.
- Weakest / most ambiguous: **bullpen** and **rest_schedule** (+0.00077 to +0.00127 log loss when removed) -- small, and their relative rank flips between the expanding window and the two rolling windows. The incremental view additionally shows adding `bullpen` on top of `team+starter` slightly *worsening* log loss (+0.00050), which the leave-one-out view alone would not have surfaced -- evidence of redundancy between `bullpen` and `rest_schedule` (both encode recent bullpen usage, at different windows).
- No family qualifies for REMOVE: removing any family increased log loss in every window and both ablation methods. Verdicts: `team` KEEP, `starter` KEEP, `bullpen` INVESTIGATE, `rest_schedule` INVESTIGATE (redundancy between the two, not evidence either is harmful).

**Leakage concerns.** None found; not escalated as P0/P1. Two checks, both
recorded in the markdown report Section 4: (1) manual review -- every family
comes from an already leakage-tested point-in-time builder (FEAT-002/003/004),
target isolation is structural (`row["target"]` vs `row["features"]`), and no
column name among the 240 resembles a postgame-derived quantity; (2) empirical
-- a new per-column univariate ROC-AUC scan
(`experiments.ablation._univariate_leakage_scan`) over all 12,118 labeled
development rows (2026 asserted absent) flagged **0 columns** at a 0.65 AUC
threshold; the strongest column was 0.5801 AUC
(`diff_team_run_diff_total_before`), barely above the full model's own 0.584
AUC and exactly the kind of cumulative team-quality differential domain
knowledge predicts, not a leakage smell.

**Commands run:**
```
python -m pytest tests/unit/experiments/test_ablation.py -q      # 12 passed
python -m pytest tests/unit/experiments/ -q                       # 34 passed
python scripts/ml012_feature_ablation.py \
    --database "<main-repo>/data/mlb.duckdb"                      # real run, 262.9s
python -m pytest -q                                                # 817 passed, 5 xfailed
```
Note: this worktree has no local `data/mlb.duckdb` (gitignored, not checked
out into a fresh worktree). The real run used the main repo checkout's
already-certified, read-only DuckDB file
(`C:/Users/sfkim/OneDrive/Desktop/sideproj/predictions-1/data/mlb.duckdb`) via
`--database`; no write connection was opened, nothing in that file was
modified.

**Test results.** `tests/unit/experiments/test_ablation.py`: 12/12 passed --
family/variant column-membership assembly (exact set equality, not just
"runs"), an explicit test that `run_ablation` raises on any column outside
the known taxonomy, a leakage test mutating every 2024/2025 feature row to an
extreme value and proving the 2022 expanding-fold metrics are byte-identical
before/after (mirrors `tests/leakage/test_calibration_leakage.py`'s mutation
pattern; kept under `tests/unit/experiments/` per this task's allowed-files
list rather than `tests/leakage/`, which is not in that list), a
2026-holdout-row-never-selected check across all three window schemes, a
leakage-scan-rejects-2026-input test, and a determinism test asserting two
`run_ablation` calls on the same matrix produce byte-identical
`fold_metrics`/`variant_aggregates`/`sanity_check_*`/`leakage_scan`/`families`.
Full repo suite: `python -m pytest -q` -> **817 passed, 5 xfailed** (all 5
xfails pre-exist this change; verified none are new).

**Known limitations:**
- Leave-one-out and incremental ablation can disagree on a family's marginal
  value when families are informationally redundant (bullpen vs.
  rest_schedule); this study surfaces that disagreement as a finding rather
  than resolving which framing is "correct."
- The univariate leakage scan only screens single-column strength; it cannot
  catch a leak that only appears jointly across columns. The manual
  provenance review is the primary safeguard for that gap.
- Sanity-check models (logistic, random forest) only cover the expanding
  window with leave-one-out, not the full window x method matrix, per the
  task's explicit "not a new comparison project" scope.
- All effect sizes are small in absolute terms, consistent with the
  already-documented weak overall V1 edge
  (`reports/experiments/v1-model-diagnostics.json`).

**Top-3 recommended next experiments** (full justification in the markdown
report Section 7):
1. Park factors (season-level static venue run/HR environment) -- best
   effort/risk/value tradeoff; point-in-time-trivial (known well before any
   game at that park).
2. Handedness/platoon matchup splits (team-vs-probable-starter-handedness) --
   natural extension of the already-ingested, already-leakage-tested FEAT-002
   starter identity/handedness pipeline.
3. Per-pitcher bullpen availability/fatigue (reliever-level, not team
   aggregate) -- directly targets this study's own bullpen/rest_schedule
   INVESTIGATE finding; reuses already-ingested `silver.pitcher_appearances`
   data, no new external source.

**ADR/project-state.** No ADR change (ADR-006 untouched, as required).
`state/CURRENT.md` updated to move ML-012 from "In progress" to a completed
entry summarizing this handoff.
