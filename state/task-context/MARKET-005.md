# MARKET-005 Context Pack

## Objective

Add a consensus-confirmed shadow PLAY strategy. This is a market/play-layer
observability task, not a model-methodology change.

## Current State

- MARKET-004 is approved/ready to finalize as the separate uncertainty-adjusted
  shadow challenger. Build on it if useful, but do not conflate the strategies.
- The user clarified that the consensus-confirmed prompt should be `MARKET-005`.
- `git status --short` was clean before this task/context/status setup.

## Read First

1. `AGENTS.md`
2. `state/CURRENT.md`
3. `tasks/MARKET-005-consensus-confirmed-play-strategy.md`
4. `docs/decisions/ADR-006-v1-methodology-lock.md`
5. `docs/research/ml-013-failure-regime-and-redundancy.md`
6. `docs/research/ml-015-prospective-model-market-diagnostic.md`
7. `src/app/board.py`
8. `src/app/dashboard_analytics.py`
9. `src/app/signal_dashboard.py`
10. `src/app/uncertainty_challenger.py`
11. `streamlit_app.py`
12. relevant tests under `tests/unit/app/`

## Key Existing Semantics

- `model_probability = P(home team wins)`.
- `market_probability = no-vig market P(home team wins)`.
- `edge = model_probability - market_probability`, home-relative.
- Baseline PLAY/PASS in `src/app/board.py` is display-only:
  `abs(edge) >= DEFAULT_EDGE_THRESHOLD`, currently 0.02. Do not change it.
- Existing dashboard code may already have MARKET-004 shadow outputs. Keep
  consensus-confirmed labels/metrics separate from uncertainty-adjusted labels.

## Implementation Guidance

- Prefer pure helper functions in a focused app module.
- Reuse `market.engine.expected_value()` for flat $1 EV.
- Reuse existing JSONL readers and latest-per-game/journal joining where
  possible.
- Strategy comparison should be artifact-backed from prediction/journal rows,
  not from retraining or live API calls.
- Keep UI copy short and explicit: `SHADOW`, `DIAGNOSTIC`, `NOT PRODUCTION`.

## Invariants

- No model, feature, pipeline, market-math, or journal mutation changes.
- Pending games excluded from resolved denominators.
- Multiple snapshots of the same `game_pk` deduped to the latest prediction.
- Large disagreement is a risk/review state, not automatic value.
- Raw model favorite is diagnostic; consensus challenger is still not production.

## Suggested Focused Tests

```bash
PYTHONPATH=src python -m pytest tests/unit/app/test_consensus_strategy.py tests/unit/app/test_signal_dashboard.py tests/unit/app/test_dashboard_analytics.py -q
git diff --check
```

If `python`/`pytest` is missing, use a temporary target install under `/tmp`
without modifying the project venv.

## Reviewer Focus

- Verify baseline PLAY/PASS unchanged.
- Verify consensus candidate uses raw model side, not edge-selected side.
- Verify model/market neutral/agreement/disagreement boundaries.
- Verify latest-per-game and pending/resolved separation.
- Verify ROI only uses rows with valid selected-side odds.
- Verify UI language does not imply production approval or betting advice.
