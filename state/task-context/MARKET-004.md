# MARKET-004 Context Pack

## Objective

Prepare a downstream Implementer to add a read-only uncertainty-adjusted
challenger strategy for MLB moneyline daily signals. The challenger is a shadow
strategy layered beside the existing baseline PLAY/PASS display convention.

## Current Orchestrator Decision

Use `MARKET-004` instead of `MARKET-003`: `MARKET-003` already exists and is
done for `probability_to_american()` / Kalshi schema reuse. The user-facing
intent remains "Uncertainty-Adjusted PLAY Challenger".

Status is blocked until APP-014 is finalized because APP-014 currently has dirty
candidate edits in dashboard analytics/model-quality files. Sequence this task
after APP-014 merge or clean rebase.

## Read First

1. `AGENTS.md`
2. `state/CURRENT.md`
3. `tasks/MARKET-004-uncertainty-adjusted-play-challenger.md`
4. `docs/decisions/ADR-002-point-in-time.md`
5. `docs/decisions/ADR-006-v1-methodology-lock.md`
6. `docs/research/ml-013-failure-regime-and-redundancy.md`
7. `docs/research/ml-015-prospective-model-market-diagnostic.md`
8. `src/app/board.py`
9. `src/app/dashboard_analytics.py`
10. `src/app/signal_dashboard.py`
11. `streamlit_app.py`
12. `src/market/engine.py`
13. relevant `tests/unit/app/` tests

## Relevant Existing Facts

- Stored `model_probability` is `P(home team wins)`.
- Stored `market_probability` is no-vig market `P(home team wins)`.
- Stored `edge` is `model_probability - market_probability`, home-relative.
- `src/app/board.py` documents `DEFAULT_EDGE_THRESHOLD = 0.02` as a synthetic,
  display-only `abs(edge) >= threshold` PLAY label. Do not change it.
- Current APP-013 helpers already distinguish raw model favorite from
  edge-selected side:
  - `dashboard_analytics.model_predicted_home()`
  - `dashboard_analytics.picked_home()`
  - `dashboard_analytics.selected_side_probability()`
  - `signal_dashboard.derive_model_side()` currently follows `picked_home`;
    do not assume its "model side" label means raw model favorite.
- `resolved_prediction_rows()` already joins predictions to the result journal,
  keeps the latest prediction per `game_pk`, and excludes pending games.
- `market.engine.expected_value()` already computes EV per 1 unit from American
  odds and a supplied win probability.
- ML-015 conclusion must be stated carefully:
  - `MODEL LAYER: not currently the primary suspect`
  - `MARKET / PLAY LAYER: more concerning`
  - do not state the live sample proves the strategy change.

## Likely Implementation Shape

- Prefer a new focused module such as `src/app/uncertainty_challenger.py` with
  pure helpers:
  - raw model favorite side
  - selected-side probabilities
  - bucket assignment
  - Wilson interval
  - uncertainty profile from resolved rows
  - challenger row preparation for current board rows
  - shadow strategy comparison for resolved rows
- Wire the module into `src/app/signal_dashboard.py` and `streamlit_app.py` as a
  separate `Uncertainty-Adjusted Signals` section.
- Consider a dedicated page only if the homepage becomes crowded after APP-014.

## Invariants

- No model retraining, retuning, recalibration, feature generation changes, or
  prediction pipeline changes.
- No mutation of `state/predictions/*.jsonl`.
- No baseline PLAY/PASS behavior change.
- Pending games excluded from resolved metrics.
- Latest prediction per `game_pk` only.
- Do not call live network APIs.
- Treat all reported numbers as artifact-backed or explicitly unavailable.

## Expected Tests

Run focused tests first:

```bash
PYTHONPATH=src python -m pytest tests/unit/app/test_uncertainty_challenger.py tests/unit/app/test_signal_dashboard.py tests/unit/app/test_dashboard_analytics.py -q
git diff --check
```

Broaden if integration risk warrants it:

```bash
PYTHONPATH=src python -m pytest tests/unit/app tests/unit/market -q
```

## Avoid

- Editing APP-014 dirty files before APP-014 is finalized.
- Reusing `MARKET-003` as the task ID.
- Presenting challenger results as validated profitability.
- Calling the uncertainty band a precise confidence interval unless the method
  label and limitations are explicit.
- Adding staking/Kelly/automated betting.

## Worktree

Recommended after APP-014:

- Branch: `agent/MARKET-004-uncertainty-challenger`
- Worktree: sibling `predictions-1-wt-MARKET-004-uncertainty-challenger`

## Open Reviewer Focus

- Verify no baseline threshold or prediction generation changed.
- Verify raw model favorite, not edge sign, controls challenger side.
- Verify low-sample uncertainty blocks promotion.
- Verify pending/multiple snapshots are not counted in resolved metrics.
- Verify language separates model quality from strategy/selection outcomes.
