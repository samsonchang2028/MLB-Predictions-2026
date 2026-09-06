# MARKET-005 — Consensus-Confirmed PLAY Strategy

## Status

done

Implemented, repaired after reviewer/tester findings, re-reviewed APPROVE, and re-tested PASS. This task supersedes the user's misnumbered "MARKET-004" consensus-confirmed prompt; MARKET-004 remains the separate uncertainty-adjusted shadow challenger.

## Dependencies

- MARKET-001
- OBS-002
- APP-013
- APP-014
- MARKET-004
- ML-013
- ML-015

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` when available

## Goal

Create a read-only challenger PLAY strategy based on model-market directional
agreement rather than pure disagreement. Do not replace the existing baseline
PLAY/PASS rule, change the locked V1 model, retrain, alter feature generation,
or mutate prediction artifacts.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `state/repo-map.md`
- `state/task-context/MARKET-005.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `docs/research/ml-013-failure-regime-and-redundancy.md`
- `docs/research/ml-015-prospective-model-market-diagnostic.md`
- `src/market/engine.py`
- `src/app/board.py`
- `src/app/dashboard_analytics.py`
- `src/app/signal_dashboard.py`
- `src/app/uncertainty_challenger.py`
- `streamlit_app.py`
- relevant app/market/journal tests

## Allowed files

- new focused helper module under `src/app/` or a narrow additive extension to
  `src/app/uncertainty_challenger.py`
- `src/app/signal_dashboard.py`
- `streamlit_app.py`
- `src/app/betting_results_page.py` or `src/app/prospective_evaluation_page.py`
  if the strategy-comparison table belongs there
- focused tests under `tests/unit/app/`
- this task file
- `state/task-context/MARKET-005.md`
- `state/agents/MARKET-005.md`

## May modify if necessary

- `src/app/dashboard_analytics.py` for shared strategy aggregation if this is
  the smallest compatible path after APP-014/MARKET-004
- `tasks/index.md` and `state/CURRENT.md` for state updates

## Do not modify

- `src/models/`
- `src/features/`
- `src/pipelines/`
- `src/observability/journal.py`
- baseline `src/market/engine.py` no-vig/edge math
- `state/predictions/*.jsonl`
- locked V1 methodology / ADR-006
- existing `DEFAULT_EDGE_THRESHOLD` and baseline PLAY/PASS logic

## Inputs

- `state/predictions/daily.jsonl`
- `state/predictions/journal.jsonl`
- existing dashboard/load helpers
- existing market EV helper
- ML-013/ML-015 reports as interpretation context only

## Outputs

- Consensus classification helper
- `consensus_confirmed_play` shadow strategy
- Strategy comparison for baseline edge-selected PLAY, raw model favorite, and
  consensus-confirmed challenger
- Dashboard section or page labeled `Strategy Comparison`
- Focused deterministic tests

## Requirements

- Keep these concepts separate in code/UI:
  - `raw_model_side`: side implied by `model_probability >= 0.5`
  - `market_side`: side implied by `market_probability >= 0.5`
  - `edge_selected_side`: side implied by `edge >= 0`
- Add model/market relationship buckets:
  - `MODEL_MARKET_AGREE_HOME`
  - `MODEL_MARKET_AGREE_AWAY`
  - `MODEL_HOME_MARKET_NEUTRAL`
  - `MODEL_AWAY_MARKET_NEUTRAL`
  - `MODEL_MARKET_DISAGREE`
  - `LARGE_MODEL_MARKET_DISAGREEMENT`
  - `MISSING_MARKET`
- Centralize and document thresholds:
  - `market_neutral_band = 0.02`
  - `large_disagreement_threshold = 0.08`
  - `min_model_side_probability = 0.55`
  - `min_selected_side_edge = 0.01`
- Define `consensus_confirmed_play` as a conservative shadow candidate:
  - selected side is always the raw model favorite
  - market agrees with raw model side, or market is neutral and does not
    strongly contradict the model
  - selected-side model probability meets the minimum confidence
  - selected-side edge is above the positive margin
  - odds are available and fresh when freshness metadata exists
  - no major data warning is present
- For selected-side display:
  - HOME: model `p`, market `p`, edge `model - market`
  - AWAY: model `1-p`, market `1-market`, edge selected model minus selected
    market
- Backtest/evaluate existing prediction and journal artifacts with latest
  prediction per `game_pk`, resolved games only for W/L and ROI, pending games
  excluded from resolved denominators.
- Report each strategy separately:
  - baseline edge-selected PLAY
  - raw model favorite
  - consensus-confirmed challenger
- Include candidate count, resolved count, wins, losses, pending, win rate,
  ROI/units when odds exist, average selected-side model probability, average
  selected-side market probability, average selected-side edge, average odds,
  home/away split, and favorite/underdog split.
- Add subset comparison for agreement, neutral, disagreement, large
  disagreement, favorite/underdog, model probability buckets, and edge buckets
  where practical without creating a generic analytics framework.
- Dashboard copy must clearly state:
  - baseline PLAY follows model-market disagreement
  - consensus-confirmed PLAY requires model/market directional confirmation
  - raw model favorite is diagnostic, not a betting strategy
  - challenger is `SHADOW`, `DIAGNOSTIC`, and `NOT PRODUCTION`
- Front-page labels should distinguish agreement/contradiction states where
  practical:
  - `MODEL-MARKET AGREEMENT`
  - `MODEL STRONGER THAN MARKET`
  - `MARKET CONTRADICTS MODEL`
  - `LARGE DISAGREEMENT — REVIEW`
  - `NO EDGE`

## Critical correctness constraints

- Do not train, retune, recalibrate, or change locked V1 methodology.
- Do not change baseline PLAY/PASS threshold or behavior.
- Do not mutate immutable prediction/journal artifacts.
- Do not use pending games in resolved metrics.
- Do not count multiple snapshots for one `game_pk`.
- Do not treat large disagreement as automatic value.
- Do not add staking/Kelly/automated betting.

## Acceptance criteria

- Predictions can be classified by model/market agreement.
- Consensus-confirmed challenger exists as shadow analytics.
- Existing PLAY/PASS remains unchanged.
- Strategy comparison reports baseline PLAY, raw model favorite, and consensus
  challenger separately.
- Metrics use latest-per-`game_pk` deduplication unless explicitly documented
  otherwise.
- Resolved and pending games are separated.
- Dashboard explains baseline disagreement vs consensus confirmation.
- Large disagreement is a review/risk state, not automatic value.
- Focused tests pass.
- `git diff --check` passes.

## Required tests

- raw model side derivation
- market side derivation
- market-neutral classification
- model/market agreement classification
- large disagreement classification
- HOME selected-side probability transformation
- AWAY selected-side probability transformation
- challenger candidate when model and market agree
- challenger no-candidate when market strongly contradicts model
- challenger no-candidate when model probability is below threshold
- challenger no-candidate when selected-side edge is below threshold
- pending games excluded from resolved denominators
- latest-per-`game_pk` deduplication if touched
- empty/small sample behavior
- missing odds behavior
- ROI calculation only when valid odds are available

## Handoff

Report files changed, helper functions added, strategy definitions, thresholds
used, artifact sources used, deduplication policy, metrics for the three
strategies, dashboard sections changed, tests added, commands run, missing data
that prevented ROI/EV reporting, and remaining limitations.
