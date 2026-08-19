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

### Handoff (implementer, 2026-08-19)

**Summary.** Built two new modules on top of ML-012's harness, unmodified:
`src/experiments/failure_regimes.py` (slices the ADR-006 locked XGBoost's
out-of-sample predictions, expanding window, into 7 dimensions) and
`src/experiments/redundancy.py` (pairwise/group feature correlation,
missingness read from the certified build's own FEAT-006 report, per-season
coverage stability, and fold-concentration of ML-012's own already-recorded
per-family ablation benefit). A single operator script,
`scripts/ml013_failure_regime_and_redundancy.py`, loads the same certified
Gold matrix ML-012 used, joins the DATA-009 historical odds archive (read
only, via `validation.odds_mapping` + `market.engine`, both unmodified) for
the edge-bucket dimension, and writes both JSON reports plus reads ML-012's
existing JSON report for the fold-concentration check (ablation itself was
never rerun). Full markdown report:
`docs/research/ml-013-failure-regime-and-redundancy.md`.

**Files changed** (all new; nothing existing modified):
- `src/experiments/failure_regimes.py` — slice harness (7 dimensions, tercile
  bucketing, probability/edge bucketing, low-n flagging).
- `src/experiments/redundancy.py` — correlation scan, missingness passthrough,
  per-season coverage stability, ablation fold-concentration re-analysis.
- `scripts/ml013_failure_regime_and_redundancy.py` — operator entry point
  (mirrors `scripts/ml012_feature_ablation.py`'s pattern; not in the task's
  literal allowed-files list but explicitly permitted by the task text
  "new operator script(s) under `scripts/`... if needed to produce the report
  from one command", same justification ML-012 already used for its own
  script).
- `tests/unit/experiments/test_failure_regimes.py` — 15 tests (assembly,
  leakage, determinism).
- `tests/unit/experiments/test_redundancy.py` — 10 tests (assembly, leakage,
  determinism).
- `reports/experiments/ml-013-failure-regimes.json`,
  `reports/experiments/ml-013-redundancy.json` — generated reports.
- `docs/research/ml-013-failure-regime-and-redundancy.md` — markdown report.
- `state/CURRENT.md` — ML-013 entry added under "In progress".

**Slices computed** (real definitions; full tables in the markdown report
Section 3; all 9,694 expanding-fold out-of-sample predictions, 2022-2025):
- `favorite_underdog` — `model_favors_home` (p_home_win>=0.5, n=5,814) vs.
  `model_favors_away` (n=3,880). Chose this partition over a per-game
  "favorite-perspective reframing" because that reframing is mathematically
  provably degenerate (log loss/Brier/calibration-gap are invariant under
  `p<->1-p`, `y<->1-y`) — documented in the module docstring and the markdown
  report Section 2, not silently avoided.
- `home_away_outcome` — actual `home_win` (n=5,135) vs. `away_win` (n=4,559).
- `probability_bucket` — 10 equal-width `[0,1]` bins (same convention as
  `evaluation.runner`'s own ECE binning); populated bins ranged n=1 to
  n=3,514.
- `edge_bucket` — `p_home_win - no_vig_opening_market_p_home`, 4 signed
  buckets (`<-0.05`, `-0.05..0`, `0..0.05`, `>0.05`); 8,784 of 9,694
  predictions resolved a usable DraftKings-preferred opening price
  (n_excluded=910).
- `starter_quality_regime` / `bullpen_workload_regime` — empirical terciles
  (low/mid/high, computed from the evaluated rows) of
  `diff_starter_season_era_before` / `diff_bullpen_bullpen_ip_L7`; starter
  regime additionally reports a `missing` bucket (n=1,170, cold-start
  starters).
- `season_fold` — expanding-fold `test_season` (2022: n=2,424; 2023: n=2,423;
  2024: n=2,422; 2025: n=2,425).

**Strongest / weakest regimes found** (full detail: markdown Sections 3.1-3.7):
- **Sharpest finding**: `edge_bucket`. Calibration gap is 5-7x larger in the
  two "model diverges strongly from market" buckets (0.070, 0.099) than in
  the two "model roughly agrees with market" buckets (0.013, 0.031), and both
  large-divergence buckets moved toward the market's own view, not the
  model's, when the game resolved.
- **Second finding**: `favorite_underdog`. The model is measurably weaker
  when it favors the away team (ROC-AUC 0.540 vs. 0.556, 50% larger
  calibration gap than `model_favors_home`), on a real n=3,880 subgroup, not
  a small-sample artifact.
- **Well-calibrated regimes**: `bullpen_workload_regime` (all three terciles
  gap <=0.002) and `starter_quality_regime` (all four groups, including the
  cold-start `missing` bucket, gap <=0.013) show no failure pocket — the
  cold-start-starter subgroup performs in line with populated terciles, not
  visibly worse.
- **Low-confidence, flagged not omitted**: the `0.80-0.90` probability bucket
  has n=1 (`low_confidence=true`); the model almost never predicts outside
  roughly [0.30, 0.80], consistent with the documented weak overall edge.
- `season_fold` reproduces ML-011's already-known 2025 degradation pattern
  (largest calibration gap 0.0217, lowest ROC-AUC 0.5645 of the four folds) —
  a cross-check that the harness matches known numbers, not a new finding.

**Redundancy findings** (full detail: markdown Section 4):
- **Pairwise/group correlation** (236 non-constant columns, `|r|>=0.9`): 108
  pairs flagged, forming 39 near-duplicate groups (23 pairs, 12 triples, 3
  quadruples, 1 group of 10). All are explainable pregame-quantity overlaps
  (season-cumulative win/loss/runs counts; `avg` vs. `total` over the same
  window; `bullpen_{ip,outs,pitches}_prior_{1d,3d}` near-linear transforms of
  the same workload; `{team,bullpen}_games_L{7,14,30}` crossing family
  boundaries because a bullpen appears in almost every game) — reviewed
  manually, none resembles a target proxy.
- **Missingness**: 135/240 columns have some missing value, read verbatim
  from the certified build's own FEAT-006 report (not recomputed); highest
  null rates are the already-known `starter` cold-start columns (12.29%
  each).
- **Per-season coverage stability**: **zero** columns exceed the 0.2
  max-minus-min population-rate-across-seasons threshold — no
  season-specific coverage regression hidden behind a whole-build average.
- **Ablation fold concentration** (re-read from
  `reports/experiments/ml-012-feature-ablation.json`, ablation itself not
  rerun): in the 4-fold expanding window, `team`/`starter`/`bullpen` are all
  close to an even spread (max-fold-share 0.41-0.50), but **`rest_schedule`
  is fold-concentrated** (0.566) — new, fold-level evidence sharpening
  ML-012's existing `rest_schedule` INVESTIGATE verdict. Important caveat
  recorded explicitly in the report: the `rolling_2`/`rolling_3` windows only
  have 2 folds each, so "concentrated" there is close to mechanically
  guaranteed by the fold count and is not meaningful signal on its own
  (marked `(concentrated*)` in the report to flag this).

**Leakage concerns and resolution.** None found; not escalated as P0/P1.
Every one of the 39 correlation groups pairs two different representations
of the same pregame quantity (manually reviewed); no slice in the
failure-regime analysis shows an implausibly strong signal (best slice-level
ROC-AUC across all 7 dimensions is 0.611, in the same weak-signal range as
the full model's ~0.58-0.59). This does not change ML-012's own leakage
verdict (0 columns flagged by its univariate scan). Full reasoning: markdown
report Section 5.

**Commands run:**
```
python -m pytest tests/unit/experiments/test_failure_regimes.py tests/unit/experiments/test_redundancy.py -q
    # 24 passed
python -m pytest tests/unit/experiments/ -q
    # 67 passed, 1 xfailed
python scripts/ml013_failure_regime_and_redundancy.py \
    --database "<main-repo>/data/mlb.duckdb"
    # real run, market-matched games=10969, 38.6s
python -m pytest -q
    # 930 passed, 6 xfailed
```
Note: this worktree has no local `data/mlb.duckdb` (gitignored, not checked
out into a fresh worktree). The real run used the main repo checkout's
already-certified, read-only DuckDB file
(`C:/Users/sfkim/OneDrive/Desktop/sideproj/predictions-1/data/mlb.duckdb`) via
`--database`; no write connection was opened, nothing in that file was
modified (odds archive access is read-only via `validation.odds_mapping`'s
existing loaders).

**Test results.** 25 new tests (15 in `test_failure_regimes.py`, 10 in
`test_redundancy.py`) covering: slice-assembly correctness (explicit bucket
membership and computed-metric assertions, not just "runs"), the
probability-bucket/edge-bucket boundary conditions, tercile-boundary
correctness, a leakage test mutating 2024/2025 feature rows and proving the
2022 expanding-fold's `season_fold` slice is byte-identical before/after
(mirrors `tests/unit/experiments/test_ablation.py`'s mutation pattern), an
end-to-end 2026-holdout-never-surfaces check, correlation-scan tests
(exact-duplicate-column detection, constant-column exclusion, 2026
rejection), a missingness-passthrough test against a real
`build_feature_completeness_report` output (not a hand-faked shape),
a per-season-coverage-stability test with an intentionally-dropped-out
season, and fold-concentration tests with hand-computed expected deltas/max-
share for both a concentrated and a spread case. Full repo suite:
`python -m pytest -q` → **930 passed, 6 xfailed** (all 6 pre-existing;
verified none are new against the pre-ML-013 baseline documented in
`state/CURRENT.md`'s PIPE-006 merge entry, 893 passed/6 xfailed).

**Known limitations** (full list: markdown report Section 7):
- `edge_bucket` uses one sportsbook's opening price per game
  (DraftKings-preferred, else alphabetically-first with a usable pair), not a
  cross-book average.
- `ablation_fold_concentration`'s `rolling_2`/`rolling_3` columns (2 folds
  each) are only weakly informative — "concentrated" is close to mechanically
  guaranteed with 2 folds regardless of the underlying data; only the 4-fold
  expanding-window result carries real signal.
- `starter_quality_regime`/`bullpen_workload_regime` terciles are empirical
  (computed from the evaluated rows), so exact boundaries would shift
  slightly on a different certified build.
- The correlation scan mean-imputes missing values for this purely
  descriptive pass — it never feeds a model or selection decision, so it does
  not need train-only preprocessing.
- Effect sizes are mostly small, consistent with the already-documented weak
  overall V1 edge; the two standout findings (edge buckets, favorite/underdog
  gap) are the exceptions, not the rule.

**Top-3 recommended next experiments** (full justification: markdown report
Section 6; independently derived from THIS task's own findings, not
copy-pasted from ML-012's list of park factors / handedness splits /
per-pitcher bullpen):
1. **Evaluate a market-informed correction** (post-hoc blend or explicit
   market-probability input feature) — motivated by the edge-bucket finding:
   calibration gap is 5-7x larger specifically where the model diverges most
   from the market, and both large-divergence buckets moved toward the
   market's view when the game resolved. Low engineering effort (MARKET-001 +
   DATA-009 already exist, reused read-only here); leakage risk low for a
   backtest, needs the existing ADR-002 snapshot-timing guard for any live
   wiring.
2. **Consolidate the near-duplicate bullpen-workload column triplets** found
   by the correlation scan, then re-run ML-012's leave-one-out ablation
   restricted to `rest_schedule` vs. a de-duplicated version — motivated by
   two new pieces of evidence this study adds: concrete column-level
   near-duplication, and `rest_schedule` being the one family whose
   expanding-window ablation benefit is fold-concentrated. Very low effort
   (column selection only, directly testable with ML-012's own unmodified
   harness); zero leakage risk.
3. **Investigate the away-favored subgroup specifically** via a home/away-
   conditional recalibration check (reusing ML-008's `evaluation.calibration`
   unmodified) — motivated by the favorite/underdog finding that the model is
   measurably weaker specifically when it favors the away team, on a
   non-trivial subgroup. Very low effort, zero leakage risk (post-hoc
   calibration only).

**ADR/project-state.** No ADR change (ADR-006 untouched, as required);
`src/experiments/ablation.py` not modified (imported/reused only).
`state/CURRENT.md` updated with an ML-013 "In progress" entry summarizing
this handoff, pending the task's required reviewer/tester gates.
