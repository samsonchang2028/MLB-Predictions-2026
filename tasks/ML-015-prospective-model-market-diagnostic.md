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

Record: summary, files changed, N of eligible/resolved predictions actually
available (this determines how much of the analysis is meaningful),
all 5 analysis parts' key numbers, historical-comparison deltas, any data-
quality issues found in the journal/enrichment data itself, the final
conclusion label with justification, and recommended measurements for the
next 2-4 weeks.
