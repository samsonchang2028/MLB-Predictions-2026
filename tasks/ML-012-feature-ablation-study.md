# ML-012 — Feature-family ablation study

## Status

backlog

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
