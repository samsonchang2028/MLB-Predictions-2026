# ML-013 — Failure-regime and redundancy research (extends ML-012)

## Status

ready

## Dependencies

- ML-012 (feature-family ablation — already established the real family
  taxonomy, KEEP/INVESTIGATE verdicts, and a clean leakage scan; this task
  builds on that harness rather than re-deriving it)
- ML-009 (locked ADR-006 baseline)
- FEAT-006 (Gold completeness gate)

## Execution

Primary role: `implementer`

Review required: `yes` (leakage risk explicitly in scope; also verify no
overlap/duplication with ML-012's already-completed work)

Tester required: `yes`

Worktree required: `yes`

## Goal

Two things ML-012 did not cover:

1. **Failure-regime analysis** — where does the locked V1 XGBoost do well vs.
   poorly, sliced by favorite/underdog, home/away, model-probability buckets,
   edge buckets, starter-quality/confidence regimes, bullpen-workload
   regimes, and season/fold. For each meaningful slice: sample size, average
   predicted probability, actual win rate, calibration gap, log loss, Brier
   score.
2. **Deeper redundancy analysis** — beyond ML-012's single-column univariate
   leakage scan: correlated/near-duplicate features *within* and *across*
   families, high-missingness columns, unstable per-season coverage, and any
   feature whose apparent ablation benefit (per ML-012's report) is
   concentrated in a single fold rather than consistent.

Then produce ranked-with-justification top-3 recommended next
information-source experiments (do not just repeat ML-012's list without
re-justifying against this task's own findings).

This is research/analysis only — same constraints as ML-012: no production
model, feature, or ADR-006 change; no 2026 use for any selection decision.

## Read first

- `AGENTS.md`, `state/CURRENT.md`
- `tasks/ML-012-feature-ablation-study.md` (full handoff — read this
  **before** touching any code; do not recompute what it already measured)
- `reports/experiments/ml-012-feature-ablation.json` and
  `docs/research/ml-012-feature-ablation.md` (existing family taxonomy,
  per-fold metrics, leakage-scan results — reuse directly)
- `src/experiments/ablation.py` (existing harness — reuse its family
  taxonomy/assembly and the certified-build loading pattern, do not
  reimplement)
- `docs/decisions/ADR-006*.md`
- `src/evaluation/splits.py`, `src/evaluation/runner.py`
- `src/evaluation/calibration.py`
- `reports/experiments/v1-repaired-a910017bac839af5.json`,
  `reports/experiments/v1-model-diagnostics.json` (existing baseline/
  diagnostic numbers — do not re-derive from scratch)

## Allowed files

- new module(s) under `src/experiments/` (e.g. `failure_regimes.py`,
  `redundancy.py`) — import and reuse `src/experiments/ablation.py` rather
  than duplicating family taxonomy or fold logic
- new tests under `tests/unit/experiments/`
- new report artifact(s) under `reports/experiments/` (JSON) plus a markdown
  research report under `docs/research/`
- new operator script(s) under `scripts/` (matching the
  `scripts/ml012_feature_ablation.py` pattern), if needed to produce the
  report from one command

## Do not modify

- `src/features/*.py`, `src/models/*.py`, ADR-006 locked config
- `src/experiments/ablation.py` itself (import/reuse, do not edit — if a
  genuine bug is found in it, report it, do not silently patch it here)
- `src/pipelines/`, `scripts/daily_predictions.py`, any Streamlit `src/app/`
  files, `src/ingestion/`, `src/market/`
- the final 2026 holdout — untouched by any part of this study

## Inputs

- the same certified Gold feature matrix ML-012 used
  (`a910017bac839af5`, or the current equivalent — confirm via
  `reports/data-quality/gold-completeness-*.json`)
- ML-012's existing family taxonomy (team/starter/bullpen/rest_schedule —
  do not invent new families; the prompt driving this task mentions
  "recent form," "offense," "home/away context," "differentials" as
  *candidate* families, but ML-012 already confirmed the real taxonomy is
  these 4 families, with home/away/diff as a naming convention *within* each
  family, not separate families — repository truth overrides the prompt)
- the locked XGBoost model (ADR-006) as primary; logistic/RF only as sanity
  checks if useful, not a new comparison project

## Outputs

- `reports/experiments/ml-013-failure-regimes.json` — per-slice metrics
  table (sample size, avg predicted prob, actual win rate, calibration gap,
  log loss, Brier, per slice dimension)
- `reports/experiments/ml-013-redundancy.json` — correlation/redundancy
  findings (correlated pairs/groups, missingness, per-season coverage,
  fold-concentration of ML-012's per-family benefit)
- a markdown research report under `docs/research/` covering: methodology,
  failure-regime tables, redundancy findings, any leakage flags, and a
  top-3 next-experiment recommendation (each with expected new information,
  likely predictive value, point-in-time availability, engineering effort,
  leakage risk, maintenance burden — brief justification, not a full spec)

## Requirements

- Failure-regime slices computed on the same chronological 2021-2025
  development folds ML-012/ML-004 already use — do not introduce a new
  fold scheme. Report per-slice metrics per fold where sample size allows;
  do not overinterpret small-n slices (state the sample size next to every
  metric, flag anything under a reasonable size threshold as low-confidence
  rather than omitting it).
- Redundancy analysis must cover: pairwise/group correlation among Gold
  feature columns (not just ML-012's univariate-vs-target scan), per-column
  missingness rate, per-season feature coverage stability, and whether any
  family's ML-012 ablation benefit is concentrated in one fold vs. spread
  consistently (re-read ML-012's `fold_metrics`, do not rerun ablation
  itself unless a genuine gap requires it).
- If anything resembles leakage (postgame-derived value, target proxy,
  suspiciously strong single or joint predictor), stop, do not continue
  interpreting results as valid, and report as P0/P1 — same rule as ML-012.
- Do not optimize for or report simulated ROI.

## Critical correctness constraints

- No 2026 data used for any selection/interpretation decision (explicit
  assertion in the harness, same pattern as ML-012/`comparison.py`).
- Train-only preprocessing; chronological folds only; no shuffling.
- Reuse the certified, completeness-gated Gold build as-is.

## Acceptance criteria

- Failure-regime report answers, per slice: n, avg predicted probability,
  actual win rate, calibration gap, log loss, Brier — for at least
  favorite/underdog, home/away, probability-bucket, edge-bucket, and
  season/fold dimensions.
- Redundancy report identifies (or explicitly rules out) correlated/
  duplicate feature groups, high-missingness columns, and fold-concentrated
  ablation benefit, going beyond ML-012's univariate scan.
- Top-3 next-experiment recommendations, each independently justified
  against this task's own findings (not copy-pasted from ML-012).
- No change to production model selection, ADR-006, `src/experiments/ablation.py`,
  or any file outside the allowed list.

## Required tests

- unit: slice assembly is correct (explicit membership assertions per
  slice dimension, not just "runs")
- leakage: mutate 2026-only/future-fold data, prove earlier-fold slice
  metrics are unaffected (mirror ML-012's leakage-test pattern)
- regression: deterministic output given a fixed certified build

## Handoff

Record: summary, files changed, slices computed (with real definitions and
sample sizes), strongest/weakest regimes found, redundancy findings, any
leakage concerns and resolution, commands run, test results, known
limitations, and the top-3 recommended next experiments with justification.
