# MARKET-004 — Uncertainty-Adjusted PLAY Challenger

## Status

candidate — ready to finalize (Option A: shadow only)

Human confirmed Option A: keep baseline 2% PLAY/PASS; MARKET-004 is a read-only
shadow challenger for comparison, not a new betting policy. Reviewer APPROVE;
focused tester suite PASS (69 tests) after final as-of Wilson first-pitch fix.
Not committed yet. Merge separately from APP-014 unless explicitly combined.

## Dependencies

- MARKET-001
- OBS-002
- APP-013
- ML-013
- ML-015
- APP-014 (sequencing dependency; avoid dashboard analytics merge conflict)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

## Goal

Add a read-only shadow strategy that only surfaces confidence-adjusted candidates
when the raw model favorite is meaningfully confident, has positive market edge,
and its uncertainty-adjusted lower estimate still beats the market. Do not change
the locked V1 model, feature generation, prediction pipeline, existing
PLAY/PASS display rule, or historical prediction journals.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `state/repo-map.md`
- `state/task-context/MARKET-004.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `docs/research/ml-013-failure-regime-and-redundancy.md`
- `docs/research/ml-015-prospective-model-market-diagnostic.md`
- `tasks/ML-015-prospective-model-market-diagnostic.md`
- APP-014 final diff/handoff if it has not merged yet

## Allowed files

- new focused helper module under `src/app/` or `src/market/`
- `src/app/signal_dashboard.py`
- `streamlit_app.py`
- `src/app/betting_results_page.py` or `src/app/prospective_evaluation_page.py`
  if a dedicated comparison table belongs there
- `tests/unit/app/` focused helper/data-preparation tests
- `tests/unit/market/` only for pure market helper tests if needed
- `state/task-context/MARKET-004.md`
- `state/agents/MARKET-004.md`
- this task file

## May modify if necessary

- `src/app/dashboard_analytics.py`, after APP-014 is merged or rebased cleanly
- `pages/` wrappers only if a dedicated Streamlit page is cleaner than a
  homepage section
- `tasks/index.md` and `state/CURRENT.md` for task-state updates

## Do not modify

- `src/models/`
- `src/features/`
- `src/pipelines/`
- `src/observability/journal.py`
- `src/market/engine.py` baseline edge/no-vig behavior unless adding a tiny pure
  helper is clearly lower risk than duplicating it
- `state/predictions/*.jsonl`
- locked V1 methodology or ADR-006
- existing `DEFAULT_EDGE_THRESHOLD` and baseline PLAY/PASS logic

## Inputs

- `state/predictions/daily.jsonl`
- `state/predictions/journal.jsonl`
- `state/predictions/game_features.jsonl` for display/risk context only
- committed holdout/development/prospective diagnostic reports when useful
- current board rows from `app.board.load_daily_board*`

## Outputs

- Shadow challenger helper/data-preparation code
- Streamlit display section or page named `Uncertainty-Adjusted Signals` or
  `Confidence-Adjusted Candidates`
- Focused tests for helper logic and analytics preparation
- Optional JSON report only if it fits an existing report pattern without
  mutating journals

## Requirements

- Preserve conceptual separation:
  - MODEL estimates `P(home team wins)`
  - MARKET converts odds to no-vig `P(home team wins)`
  - EDGE is `model_probability - market_probability`
  - BASELINE PLAY/PASS remains the existing display convention
  - CHALLENGER is a separate shadow strategy
- Candidate side must be the raw model favorite:
  - `P(home) >= 0.5` means HOME
  - `P(home) < 0.5` means AWAY
- For the raw model favorite, compute selected-side model probability, market
  probability, edge, American odds, raw EV, lower-bound EV when odds exist, and
  wins-per-100 display values.
- First-pass rule must use configurable documented constants, not thresholds
  tuned silently on the current live sample:
  - favorite model probability at least 0.55
  - favorite edge at least 0.03
  - uncertainty lower bound exceeds market probability by at least 0.01 or 0.02
  - lower-bound EV positive when odds are available
  - no major freshness/integrity flags
- Estimate uncertainty by bucket over artifact-backed resolved latest
  predictions, deduped to latest prediction per `game_pk`, excluding pending
  games.
- Preferred buckets:
  - `50-55%`
  - `55-60%`
  - `60-65%`
  - `65-70%`
  - `70%+`
- Minimum bucket sample size: 30. Below that, flag `LOW_SAMPLE_UNCERTAINTY` and
  do not promote solely from that range.
- Use a dependency-free Wilson or Jeffreys/Beta interval unless a suitable
  existing helper exists.
- Wording must avoid overstating precision. Prefer:
  - `Expected wins per 100 similar games`
  - `Uncertainty-adjusted range`
  - `Conservative estimate`
- Dashboard table should include matchup, first pitch, raw model favorite,
  model favorite probability, expected wins per 100, uncertainty lower/upper per
  100, market-implied wins per 100, conservative edge per 100, offered odds,
  lower-bound EV, baseline label, challenger label, risk flags, and bucket N.
- Baseline PLAYs against the raw model favorite must be visibly flagged as
  `BASELINE PLAY AGAINST MODEL FAVORITE`, informational only.

## Risk flags

Implement simple flags from available artifacts:

- `STALE_ODDS`
- `MISSING_ODDS`
- `GAME_ALREADY_STARTED`
- `GAME_STARTING_SOON`
- `LOW_SAMPLE_UNCERTAINTY`
- `MODEL_PROB_NEAR_50`
- `EDGE_DOES_NOT_SURVIVE_UNCERTAINTY`
- `LARGE_MODEL_MARKET_DISAGREEMENT`
- `BASELINE_PLAY_AGAINST_MODEL_FAVORITE`
- `MISSING_REQUIRED_FIELDS`

## Analytics/reporting

If the repo has a natural report pattern after APP-014, add a shadow strategy
comparison for resolved data only:

- baseline PLAY strategy
- raw model favorite strategy
- uncertainty-adjusted challenger strategy

For each strategy, report N, wins, losses, pending, win rate, ROI/units when
artifact-backed, average selected-side model probability, average selected-side
market probability, average edge, favorite/underdog split, home/away split, edge
buckets, and model-probability buckets. Keep holdout, historical, and
prospective/live windows separated.

## Critical correctness constraints

- Do not train, retune, recalibrate, or change the locked V1 model.
- Do not change feature generation.
- Do not change baseline PLAY/PASS logic or threshold.
- Do not mutate prediction journals or immutable rows.
- Do not use pending games in resolved performance metrics.
- Do not double-count multiple prediction snapshots for the same `game_pk`.
- Do not treat edge as proof of value or baseline PLAY win rate as model
  accuracy.
- Do not add Kelly sizing, staking recommendations, automated betting, a new
  database, FastAPI, auth, or heavyweight UI tests.

## Acceptance criteria

- Baseline PLAY/PASS behavior remains unchanged.
- A separate uncertainty-adjusted challenger exists in shadow mode.
- Challenger only considers the raw model favorite side unless a documented repo
  reason says otherwise.
- Dashboard shows expected wins per 100 similar games.
- Dashboard shows an uncertainty-adjusted range or clearly states unavailable /
  low sample.
- Dashboard compares model-favorite probability to market-implied probability.
- Dashboard shows whether the lower bound still beats the market.
- Baseline PLAYs against the raw model favorite are flagged.
- Large model-market disagreements are review-worthy, not automatically good.
- Pending games are excluded from resolved performance metrics.
- Multiple prediction snapshots for one `game_pk` are not double-counted.
- Focused tests pass.
- `git diff --check` passes.

## Required tests

- raw model favorite derivation from `P(home)`
- selected-side model probability for HOME and AWAY
- selected-side market probability for HOME and AWAY
- favorite edge calculation
- baseline PLAY against raw favorite flag
- wins-per-100 and percentage-point formatting
- Wilson/Jeffreys interval helper if implemented
- bucket boundaries
- low-sample behavior
- lower-bound edge calculation
- lower-bound EV calculation
- stale/missing odds flags
- empty input behavior
- pending games excluded from resolved metrics
- latest-prediction-per-`game_pk` deduplication
- no duplicate counting across multiple prediction snapshots

## Handoff

Record:

- task ID,
- summary of changes,
- files changed,
- helpers added,
- uncertainty method used,
- bucket definitions,
- minimum sample rules,
- challenger rule implemented,
- dashboard sections changed,
- baseline PLAY behavior confirmation,
- strategy comparison metrics added,
- risk flags implemented,
- EV added or deferred,
- tests added,
- commands run,
- manual dashboard smoke-test summary,
- known limitations,
- reviewer/tester follow-ups,
- whether ADR or project-state update is needed.
