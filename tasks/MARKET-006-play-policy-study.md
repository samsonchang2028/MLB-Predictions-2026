# MARKET-006 — Production PLAY policy study and threshold search

## Status

candidate

## Dependencies

- MARKET-001 (no-vig / edge engine)
- OBS-001 / OBS-002 (prediction journal + result enrichment)
- ML-015 (prospective diagnostic evidence)
- MARKET-004 / MARKET-005 (existing shadow challengers — reuse semantics, do not conflate)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/MARKET-006-play-policy-study`

## Goal

Turn the synthetic 2% PLAY display rule into a **rigorously tested, auditable
policy study** over the canonical production journal. Determine whether any
threshold or policy family is usable out of sample on ROI/units/stability —
not win rate alone. **Do not change production PLAY behavior.**

## Read first

- `AGENTS.md`, `state/CURRENT.md`, `state/task-context/MARKET-006.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `docs/research/ml-015-prospective-model-market-diagnostic.md`
- `reports/experiments/ml-015-prospective-diagnostic.json`
- `tasks/ML-015-prospective-model-market-diagnostic.md`
- `src/app/board.py`, `src/market/engine.py`
- `src/observability/journal.py`
- `src/experiments/prospective_diagnostic.py` (reuse join/dedup patterns)
- `src/app/consensus_strategy.py`, `src/app/dashboard_analytics.py`

## Allowed files

- `src/market/play_policy.py` (new — deterministic policy pure functions)
- `src/market/policy_evaluation.py` (new — population, slices, walk-forward, ROI)
- `scripts/market_policy_study.py` (new — reproducible CLI)
- `tests/unit/market/test_play_policy.py`
- `tests/unit/market/test_policy_evaluation.py`
- `reports/market/` (generated artifacts only via CLI)
- `docs/decisions/ADR-007-play-staking-policy.md` (**proposed** draft only — not accepted)
- `state/task-context/MARKET-006.md`, `state/agents/MARKET-006.md`
- this task file

## May modify if necessary

- `tasks/index.md`, `state/CURRENT.md`, `state/repo-map.md` (state updates only)

## Do not modify

- `src/models/`, `src/features/`, `src/pipelines/daily.py`
- ADR-006 locked methodology
- `DEFAULT_EDGE_THRESHOLD` in `src/app/board.py` (baseline must remain 0.02)
- `state/predictions/*.jsonl` (immutable — read only)
- Streamlit UI (owned by MARKET-007)
- production prediction generation

## Phases (implementer scope)

1. **Canonical evaluation population** — latest pregame snapshot per `game_pk`,
   resolved-only for outcome metrics, pending excluded, exact `(game_pk,
   prediction_timestamp)` joins, no team/date fallback.
2. **Field normalization** — expose correctly named analytics fields; if journal
   `predicted_home_win` means edge-selected side, document + normalize in read
   layer only (never rewrite immutable history).
3. **Baseline reproduction** — `abs(edge) >= 0.02` PLAY; one command reproduces
   all baseline metrics including flat 1u ROI/units.
4. **Diagnostic decomposition** — crossover vs agreement, edge buckets (0–1 … 10+ pp),
   model-confidence, market-confidence, price, home/away, favorite/underdog.
5. **Fixed candidate grid** — lower-bound thresholds + bounded edge ranges; policy
   families A–F as specified in orchestrator brief (baseline, no-crossover,
   consensus, consensus+bounded, no-crossover+cap, confidence-first).
6. **Time-ordered walk-forward validation** — chronological folds; rank on dev
   folds only; final untouched block; no random K-fold; report limitations if
   sample is small.
7. **Uncertainty** — N, win-rate CI, ROI bootstrap (deterministic seed),
   fold-by-fold tables; paired comparisons where meaningful.
8. **Overfitting guard** — record every candidate tested; distinguish exploratory
   vs confirmatory; report best in-sample / cross-validated / final-holdout.
9. **Production gates** — implement gate checks (reproducibility, robustness,
   economic usefulness); verdict one of: `POLICY CANDIDATE READY FOR PROSPECTIVE
   SHADOW VALIDATION`, `POLICY CANDIDATE READY FOR ADR REVIEW`, or
   `NO POLICY READY FOR PRODUCTION`.
10. **Reporting** — `reports/market/play-policy-study-<run_id>.json` and `.md`
    with artifact hashes, full tables, explicit conclusion.

## CLI

```bash
python scripts/market_policy_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --run-id market-006 \
  --policy baseline   # optional single-policy shortcut
```

Exit non-zero on integrity failures. Never mutate source artifacts.

## Critical constraints

- Do not optimize win rate alone; primary: flat 1u ROI, units, volume, fold stability.
- Do not promote any policy to production.
- Do not retrain or retune the V1 model.
- `NO POLICY READY FOR PRODUCTION` is a valid successful outcome.

## Acceptance criteria

- Baseline 2% PLAY reproducible from one command on local journal.
- One canonical latest-per-game population with tests for dedup/joins/pending exclusion.
- Crossover and large-disagreement slices explicitly measured.
- Fixed threshold/policy grid evaluated with walk-forward protection.
- Confidence intervals / bootstrap reported with deterministic seed.
- `src/market/play_policy.py` exposes structured decisions + reason codes.
- Focused tests pass; full relevant suite green; `git diff --check` clean.
- Versioned JSON + Markdown report with explicit production-readiness verdict.
- ADR-007 drafted as **proposed** (gates + process); not accepted unless evidence supports.

## Handoff

**Task:** MARKET-006 — Production PLAY policy study

**Summary:** Implemented deterministic policy module, canonical evaluation harness,
fixed 135-candidate grid (families A–F), walk-forward study CLI, unit tests, and
versioned reports. Baseline 2% PLAY reproduced on live journal. **No candidate
passed production gates; verdict is NO POLICY READY FOR PRODUCTION.**

**Files changed:**
- `src/market/play_policy.py` (new)
- `src/market/policy_evaluation.py` (new)
- `scripts/market_policy_study.py` (new)
- `tests/unit/market/test_play_policy.py` (new)
- `tests/unit/market/test_policy_evaluation.py` (new)
- `reports/market/play-policy-study-market-006.json` + `.md` (generated)
- `docs/decisions/ADR-007-play-staking-policy.md` (PROPOSED)
- `tasks/MARKET-006-play-policy-study.md`, `state/agents/MARKET-006.md`

**Population:** Latest pregame prediction per `game_pk` via
`load_daily_board_with_diagnostics`, exact `(game_pk, prediction_timestamp)` journal
join, pending excluded. **N = 278** resolved games (0 malformed skips). Journal
`predicted_home_win` normalized as edge-selected side on read only.

**Baseline metrics (`abs(edge) >= 0.02`):**
| Metric | Value |
|---|---|
| Plays (resolved) | 196 |
| Win rate | 40.3% |
| ROI (flat 1u) | -14.5% |
| Units | -28.37 |
| Holdout ROI (22%) | -22.5% |
| Holdout units | -9.67 |

**Crossover / disagreement (baseline plays):**
- Crossover (edge side ≠ raw model fav): 86 plays, 33.7% win, -16.0% ROI
- No crossover: 110 plays, 45.5% win, -13.3% ROI
- Model/market agree: 147 plays, 39.5% win, -15.6% ROI
- Model/market disagree: 49 plays, 42.9% win, -11.1% ROI
- Large disagreement (|model−market| ≥ 8pp): 37 plays, 35.1% win, -22.1% ROI, -8.17 units
- Not large disagreement: 159 plays, 41.5% win, -12.7% ROI, -20.20 units

**Walk-forward design:** Chronological order by `game_start_timestamp`; 3 dev
folds + 22% final holdout (61 games). Rank on dev folds: ROI → units → n → fold
stability. Bootstrap ROI CI (seed=42, n=1000); Wilson win-rate CI.

**Candidates tested:** 135 fixed-grid policies (10 baseline thresholds, 10
no-crossover, 6 consensus, 66 consensus+bounded, 29 no-crossover+cap, 6
confidence-first).

**Top dev-ranked (holdout ROI shown; all failed gates):**
1. `no_crossover_cap_0.05_min_0.040` (E): dev ROI +30.0%, holdout -35.5% (n=3)
2. `no_crossover_cap_0.05_min_0.030` (E): dev +23.6%, holdout -19.0% (n=7)
3. `no_crossover_cap_0.05_min_0.035` (E): dev +15.7%, holdout -24.4% (n=5)

**Shadow candidate:** None (production gates not passed).

**Verdict:** `NO POLICY READY FOR PRODUCTION`

**Tests/commands:**
```bash
.venv/bin/python scripts/market_policy_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --run-id market-006
PYTHONPATH=src pytest tests/unit/market/ -q  # 125 passed, 4 xfailed (pre-existing)
git diff --check
```

**Limitations:** Small prospective N (~278); holdout slices often below
LOW_N_THRESHOLD=30; positive holdout ROI on top candidates is likely small-sample
noise given negative dev-fold means.

**MARKET-007 blockers:** No gated shadow candidate to promote. MARKET-007 can
still wire display-only baseline vs top exploratory policies with NOT PRODUCTION
labels using `play_policy.py` API (`evaluate_policy`, `classify_game`,
`policy_reason_codes`).
