# ML-015 — Prospective model and market diagnostic study

## Status

ready

## Dependencies

- OBS-001 (append-only prediction journal)
- OBS-002 (result enrichment operator — resolved outcomes)
- MARKET-001 (no-vig/edge engine)
- ML-009 (locked ADR-006 baseline — the model under monitoring)
- ML-010 (final 2026 holdout — historical comparison point)

## Execution

Primary role: `implementer`

Review required: `yes` (this is a monitoring/diagnostic report whose
conclusions could be misread as grounds for changing the locked model —
review must confirm no model/threshold change occurred and that
prospective-sample conclusions are appropriately hedged)

Tester required: `yes`

Worktree required: `yes`

## Goal

The live dashboard has been reporting roughly a 39% PLAY win rate over
~50-60 finished plays in its first week of prospective (real, not
backtested) production predictions. Determine whether this appears to
originate from (1) probability-model performance, (2) market disagreement/
edge calculation, (3) PLAY selection behavior, (4) small-sample variance, or
(5) a specific failure regime — **without retuning, recalibrating, or
otherwise changing the locked V1 model**. This is monitoring/diagnosis, not
model selection: prospective data must not be used to pick features,
hyperparameters, calibration method, or PLAY/PASS thresholds.

**Important repo-truth note**: `src/app/board.py` (see its own module
docstring, ~line 15-19) states plainly that PLAY/PASS is a **synthetic,
display-only** threshold (`DEFAULT_EDGE_THRESHOLD = 0.02`, `abs(edge) >=
edge_threshold`) — there is no PLAY/PASS concept in `src/market/` or
`src/pipelines/daily.py` itself. Read that docstring before treating "PLAY"
as a first-class pipeline concept; it is a board-display convention layered
on top of the model probability + market edge, exactly the kind of
selection-layer-vs-probability-layer distinction this task needs to keep
separate per its own stated interpretation rule.

## Read first

- `AGENTS.md`, `state/CURRENT.md`
- `docs/decisions/ADR-006*.md`, any ADR-002-equivalent point-in-time-odds doc
- `src/observability/journal.py` (the append-only prediction journal —
  understand its record schema before querying it)
- `scripts/enrich_prediction_results.py` (OBS-002 — how resolved outcomes get
  attached to prediction records; this is the actual/predicted join point)
- `src/app/board.py` (PLAY/PASS display convention — see note above)
- `src/market/` (no-vig/edge engine — MARKET-001)
- `src/app/performance.py`, `src/app/performance_page.py` (existing Streamlit
  metrics — understand what's already computed/displayed before recomputing)
- `reports/experiments/v1-holdout-2026.json` (locked 2026 holdout numbers —
  the historical comparison point: log loss 0.6888, Brier 0.2478, ECE
  0.0222, ROC-AUC 0.5497, accuracy 53.8%, n=1,797)
- `docs/research/ml-013-failure-regime-and-redundancy.md` (existing
  failure-regime taxonomy on historical dev folds — reuse the same slice
  definitions where applicable so prospective results are comparable
  apples-to-apples, don't invent a new slicing scheme)
- `state/predictions/` (the actual prediction journal data files this study
  reads from)

## Allowed files

- new module(s) under `src/experiments/` or a dedicated
  `src/observability/diagnostics.py` (implementer's choice, follow existing
  pattern of small focused modules)
- new tests under `tests/unit/experiments/` or `tests/unit/observability/`
  as appropriate
- new report artifact(s) under `reports/experiments/` (JSON) plus a markdown
  research report under `docs/research/`
- new operator script(s) under `scripts/` to produce the report from one
  command

## Do not modify

- `src/features/*.py`, `src/models/*.py`, ADR-006 locked config
- `src/experiments/ablation.py`, `failure_regimes.py`, `redundancy.py`
  (existing ML-012/ML-013 harnesses — import/reuse historical-comparison
  numbers already computed there rather than recomputing, do not edit)
- `src/app/board.py`'s `DEFAULT_EDGE_THRESHOLD` or any PLAY/PASS logic — this
  study observes and reports on selection behavior, it does not change it
- `src/pipelines/daily.py`, `src/observability/journal.py`,
  `scripts/enrich_prediction_results.py` (read/import from these, do not
  edit the actual production journal/enrichment code)
- `src/market/`, `src/ingestion/`
- the final 2026 holdout (comparison-only, never recomputed or altered)

## Inputs

- the live prediction journal + enriched results under `state/predictions/`
  (first week of real prospective predictions — actual production data, not
  a backtest)
- the locked 2026 holdout numbers (`reports/experiments/v1-holdout-2026.json`)
  and historical walk-forward numbers as comparison points only
- ML-013's existing failure-regime slice definitions, reused for consistency

## Outputs

- `reports/experiments/ml-015-prospective-diagnostic.json` — machine-readable:
  all-prediction metrics, PLAY-subset metrics, probability-bucket table,
  edge-bucket table, failure-regime slices, historical-comparison deltas
- a markdown research report under `docs/research/` with: executive summary,
  all-prediction model metrics, PLAY-subset metrics, probability buckets,
  edge buckets, failure-regime analysis, historical comparison, sample-size
  cautions, suspicious patterns worth monitoring, data-quality concerns,
  recommended measurements for the next 2-4 weeks, and one final conclusion
  line from the required label set (see Acceptance criteria)

## Requirements

- **Part 1 (all resolved eligible predictions, not just PLAY rows)**: N, log
  loss, Brier, accuracy, ROC-AUC (only if N supports it), average predicted
  home probability, calibration/reliability diagnostics. Compare against
  locked 2026 holdout expectations, hedged for sample size.
- **Part 2 (PLAY subset)**: N, wins, losses, win rate, average predicted
  probability of the selected side, actual selected-side win rate, average
  no-vig market probability, average edge, average odds, ROI only if actual
  wager-price semantics are valid in this repo (check — if stake/wager
  amount isn't a real recorded field, do not fabricate a ROI number), units
  only if already defined elsewhere. Directly address: does "average model
  P(selected side) vs. actual selected-side win rate" show a notable gap
  given the sample size, or is it consistent with variance at n≈50-60?
- **Part 3 (probability buckets)**: 45-50/50-55/55-60/60-65/65%+ (or the
  nearest sensible bucketing given actual data density) — N, avg predicted
  probability, actual win rate, calibration gap, log loss, Brier per bucket.
  Do not overinterpret tiny buckets (state N next to every number).
- **Part 4 (edge buckets)**: 0-2/2-4/4-6/6-8/8%+ — N, avg model probability,
  avg market probability, actual win rate, avg edge, ROI if valid. Look
  specifically at whether the largest-disagreement bucket underperforms
  (possible missing-information signal, not just weak-classifier noise).
- **Part 5 (failure regimes)**: favorite/underdog, home/away selection,
  near-50% vs higher-confidence, high-edge vs low-edge, starter uncertainty/
  missing-late-starter, bullpen workload regime, sportsbook, time-to-first-
  pitch — using only data that already exists (no new ingestion). Report N
  per slice; skip a slice cleanly (state why) rather than fabricate it if the
  underlying field doesn't exist in this repo's actual journal schema.
- Keep the three-system distinction explicit throughout the report:
  probability model (P(home)) vs. market engine (model-vs-market edge) vs.
  betting strategy (PLAY/PASS + any staking) — do not attribute a PLAY
  win-rate shortfall to "bad model calibration" unless the Part 1/3 evidence
  (not just Part 2) actually supports it.

## Critical correctness constraints

- Must not use the prospective sample to select/retune any V1/V2 feature,
  hyperparameter, calibration method, training window, or PLAY/PASS
  threshold — this is read-only monitoring.
- Must not modify the 2026 final holdout or re-derive it.
- Must not modify `state/predictions/` journal/results files — read-only
  analysis of existing records.
- If N is too small for a metric to be meaningful (e.g. ROC-AUC at n<20),
  say so explicitly rather than reporting a noisy number without caveat.

## Acceptance criteria

- Report covers all 5 required analysis parts plus historical comparison,
  each with explicit sample sizes attached to every reported statistic.
- Final conclusion is exactly one of: `NO CURRENT EVIDENCE OF MODEL
  FAILURE`, `POSSIBLE MODEL DRIFT — MORE DATA NEEDED`, `MARKET/PLAY LAYER
  APPEARS MORE CONCERNING`, `MODEL QUALITY APPEARS CONCERNING`, or
  `EVALUATION DATA INSUFFICIENT / INVALID` — justified by the report's own
  evidence, not asserted.
- No recommendation to change the locked model unless a genuine correctness
  defect (not a performance/calibration preference) is found — if one is
  found, flag it as P0/P1 exactly like ML-012/ML-013's leakage-escalation
  rule, do not silently work around it or fix it in this task.
- Does not implement: new features, new calibration, new XGBoost tuning,
  Monte Carlo, new PLAY thresholds, Kelly sizing — diagnostic only.
- No change to production model selection, ADR-006, PLAY/PASS logic, or any
  file outside the allowed list.

## Required tests

- unit: bucket/slice assembly correctness (explicit membership assertions)
- regression: deterministic output given a fixed journal snapshot
- a test confirming the report never reads/depends on 2026 holdout data as
  an input to any prospective-sample computation (comparison-only usage is
  fine; selection usage is not)

## Handoff

**Summary.** Implemented a read-only diagnostic study of the first week+ of
real, live production predictions from the ADR-006 locked model. No model,
feature, calibration, threshold, or production file was modified. All
computation reuses existing, unmodified pipeline code:
`app.board.load_daily_board_with_diagnostics` (same join/dedup/PLAY-PASS
logic the live dashboard uses), `evaluation.runner._probability_metrics`
(same formulas as the 2026 holdout report), and
`experiments.failure_regimes.slice_by` / `feature_value_by_game_pk` /
`tercile_labels` (ML-013's own slice machinery, for direct comparability).

**Files changed** (all new; nothing in the "do not modify" list touched):
- `src/experiments/prospective_diagnostic.py` -- core diagnostic module
  (row assembly/join, Parts 1-5, historical comparison).
- `scripts/ml015_prospective_diagnostic.py` -- operator entry point; reads
  `state/predictions/{daily,journal,game_features,skipped}.jsonl` (paths
  overridable via CLI flags, since `state/predictions/*.jsonl` is gitignored
  and a worktree checkout won't have it) plus
  `reports/experiments/v1-holdout-2026.json`, writes
  `reports/experiments/ml-015-prospective-diagnostic.json`.
- `tests/unit/experiments/test_prospective_diagnostic.py` -- 16 tests: 6
  synthetic games with explicit hand-computed membership (including a
  raw-favorite/edge-sign "crossover" case), a regression/determinism test,
  and 2 tests confirming the 2026 holdout is never a computation input
  (`build_report` takes no holdout parameter at all;
  `historical_comparison` is proven not to mutate its inputs).
- `reports/experiments/ml-015-prospective-diagnostic.json` -- generated
  artifact (real run against the live journal, see below).
- `docs/research/ml-015-prospective-model-market-diagnostic.md` -- full
  written report.

**Commands run**: `python -m pytest tests/unit/experiments -q` (83 passed,
1 xfailed, includes the pre-existing ML-012/013/etc. suites plus this
task's 16 new tests); `python scripts/ml015_prospective_diagnostic.py
--daily <main-repo>/state/predictions/daily.jsonl --journal
<main-repo>/state/predictions/journal.jsonl --game-features
<main-repo>/state/predictions/game_features.jsonl --skipped
<main-repo>/state/predictions/skipped.jsonl` (the worktree's own
`state/predictions/` is empty since it's gitignored; the real journal was
read read-only from the main checkout via explicit paths, same pattern
ML-012/013 used for the DuckDB file).

**N of eligible/resolved predictions**: **94** (one locked
`model_version`, deduped to the latest prediction per `game_pk` with a Final
journal result, same convention the live board itself uses). PLAY subset:
**68**. PASS: 26. Every bucket/slice below N=94 is small; every reported
statistic in the JSON carries its own N and a `low_confidence` flag
(threshold 30, reused from ML-013). This is meaningfully larger than the
~50-60 plays originally cited on the dashboard -- the sample has grown
during this task's execution window, itself informative (see Part 2 below).

**Part 1 (all resolved predictions, N=94)**: log loss 0.70125 (holdout
0.68878, delta +0.01247), Brier 0.25373 (holdout 0.24781, delta +0.00592),
ROC-AUC 0.54727 (holdout 0.54968, delta **-0.00241**, essentially
unchanged), accuracy 0.58511 (holdout 0.53812, delta +0.04699). 10-bin ECE
looks large (0.134 vs holdout 0.022) but is unreliable at N=94/10 bins; the
scalar calibration gap (`|avg_p - actual_rate|` = 0.047) is the more
trustworthy summary and is modest. **No evidence of broad model-quality
failure in the raw probability metrics.**

**Part 2 (PLAY subset, N=68)**: 30 wins / 38 losses, win rate **44.12%**
(not the ~39% originally cited -- recomputation over the now-larger sample
shows a higher, though still sub-50%, rate). Avg model P(selected side)
52.45% vs. actual selected-side win rate 44.12% -- an 8.33pp gap, ~1.4
binomial standard errors at this N (suggestive, not conclusive). The no-vig
market's own average implied probability on these same picks (46.33%) is
much closer to the actual rate (2.2pp gap) than the model's own confidence
is. ROI not computed: no stake/wager field exists in `daily.jsonl` or the
OBS-001 journal (confirmed against `src/observability/journal.py`'s schema
and `app.performance.MARKET_RELATIVE_NOTE`, the same repo-wide finding) --
fabricating one would be worse than omitting it.

**Part 3 (probability buckets)**: 6 buckets (<45%, 45-50%, 50-55%, 55-60%,
60-65%, >=65%), N from 3 to 24 per bucket, all `low_confidence`. No
monotonic miscalibration pattern; the two most-off buckets (45-50% and
>=65%) have N=24 and N=3 respectively -- the latter is not interpretable.

**Part 4 (edge buckets, selected side)**: 0-2% (N=26, 65.4% win rate),
2-4% (N=25, 52.0%), 4-6% (N=13, 30.8%), 6-8% (N=11, 63.6%), **8%+ (N=19,
31.6%)**. The highest-disagreement bucket has one of the worst win rates and
the largest average model-vs-market gap (10.9pp); this **directionally
mirrors ML-013's own historical finding on the identical edge-bucket
dimension** (`docs/research/ml-013-failure-regime-and-redundancy.md`:
calibration gap 5-7x larger where the model diverges most from the no-vig
market). Not monotonic (4-6% is also low, also low-N) so not proof, but the
same locked model reproducing the same qualitative weak spot on genuinely
new live data 8+ months later is the single most notable finding in this
study.

**Part 5 (failure regimes, raw P(home_win) framing)**: favorite/underdog,
home/away selection (PLAY pick side -- distinct from raw favorite; a
synthetic-test "crossover" case is covered explicitly in the unit tests),
home/away outcome, near-50%-vs-confident, high-edge(PLAY)-vs-low-edge(PASS),
starter-quality tercile (proxy for "starter uncertainty" -- true
missing-late-starter games never reach a prediction row, see data quality),
bullpen-workload tercile, and time-to-first-pitch all computed, N and
low_confidence stated per group; no dimension in Part 5 shows a pattern as
sharp as Part 4's edge-bucket finding. `sportsbook` and the true
"missing-late-starter" per-row slice were **skipped cleanly with stated
reasons** (single canonical sportsbook pinned per-prediction; starter-missing
games are excluded entirely upstream, never reach a resolved prediction row)
rather than fabricated.

**Historical-comparison deltas**: see Part 1 table above (log loss
+0.01247, Brier +0.00592, ECE +0.11163 [unreliable at this N], ROC-AUC
-0.00241, accuracy +0.04699). The 2026 holdout was read only for this
side-by-side display -- `build_report` takes no holdout parameter at all,
and `historical_comparison` is a separate, pure, read-only function proven
by test not to mutate its inputs.

**Data-quality issues found**: none active/blocking. One historical issue
was checked and confirmed already resolved: `state/predictions/
journal.before-edge-side-fix.jsonl` is a preserved backup of 4 pre-fix
records from commit `93787ad` ("OBS-002: score edge-side picks correctly"),
committed the same morning (07:15 local) strictly before any result
enrichment ran (14:15 that day) -- the live `journal.jsonl` this study reads
is entirely post-fix. Minor coverage gap noted, not urgent: 5 of 94 resolved
predictions lack a usable `diff_starter_season_era_before` feature value in
`game_features.jsonl` (the `starter_quality_regime` `missing` bucket),
worth checking as a follow-up if it grows. **No P0/P1 correctness defect
found; nothing escalated.**

**Final conclusion**: **MARKET/PLAY LAYER APPEARS MORE CONCERNING**.
Justification: Part 1's raw-probability-model metrics show no evidence of
broad model-quality failure (ROC-AUC essentially unchanged from the 2026
holdout, delta -0.00241), so per this task's explicit instruction the PLAY
win-rate shortfall is **not** attributed to bad model calibration. The
selection layer built on top of the model (PLAY when |edge|>=2%) shows a
specific, structurally-motivated weak spot instead: the highest-edge bucket
has one of the worst win rates in the sample (Part 4), the PLAY subset's
own model confidence overshoots its actual outcome rate by more than the
no-vig market's own gap on the same games (Part 2), and this pattern
directionally reproduces ML-013's own historical finding on the identical
edge-bucket dimension using genuinely new, independent prospective data.
Every cited number is `low_confidence` at N<=68, so this is not yet strong
enough evidence to act on, and it explicitly does not rise to "MODEL QUALITY
APPEARS CONCERNING" -- but of the two systems, the market/PLAY selection
layer is where this report's evidence points continued monitoring toward.

**Recommended measurements, next 2-4 weeks**:
1. Re-run `scripts/ml015_prospective_diagnostic.py` weekly; watch whether
   the 8%+ edge bucket's win rate stays anomalously low once its N passes
   30 (currently 19).
2. Track the Part 2 model-vs-actual gap (currently +8.33pp) over time --
   persisting/growing strengthens the market/PLAY-layer concern; closing
   toward 0 supports "it was just this week's variance."
3. Track the Part 1 ROC-AUC delta vs. the 2026 holdout (currently -0.00241,
   essentially zero) -- a delta growing substantially more negative over
   several weeks would be the first real signal of raw model-quality drift,
   distinct from the PLAY-layer concern above.
4. Track `starter_quality_regime`'s `missing` count and
   `starter_uncertainty_skip_count` as data-quality/coverage health checks.
5. Do not retune, recalibrate, or change the PLAY/PASS threshold based on
   this study alone (per ADR-006 and this task's constraints). If the
   edge-bucket pattern persists with a larger N over the next several
   weeks, the appropriate next step is a dedicated, properly-governed
   follow-up task -- not a change made inside this monitoring study.
