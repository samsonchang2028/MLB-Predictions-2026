# MARKET-006 Context Pack

## Objective

Build the production-grade PLAY **policy study** — canonical journal population,
baseline reproduction, decomposition slices, fixed candidate grid, walk-forward
validation, ROI/units, uncertainty, gates, and versioned reports. Extract pure
policy functions to `src/market/play_policy.py` for MARKET-007 to consume.

## Orchestrator verdict on scope

This task is **study + policy module only**. Streamlit changes are **MARKET-007**.
Do **not** change `DEFAULT_EDGE_THRESHOLD` or baseline board behavior.

## Current evidence (preserve in reports)

From latest ML-015 run (`reports/experiments/ml-015-prospective-diagnostic.json`):

| Slice | Win rate | N |
|---|---|---|
| All resolved (raw model framing) | ~56.5% accuracy | 278 |
| Baseline PLAY | **40.3%** | 196 |
| PASS (0–2% edge) | **58.5%** | 82 |
| Edge 4–6% | 33.3% | 45 |
| Edge 8%+ | 35.1% | 37 |

Diagnosis: **model OK, PLAY selection layer is the problem.**

## Read first (ordered)

1. `AGENTS.md`, `state/CURRENT.md`
2. `tasks/MARKET-006-play-policy-study.md`
3. `docs/research/ml-015-prospective-model-market-diagnostic.md`
4. `src/app/board.py` (PLAY/PASS semantics — do not change threshold)
5. `src/market/engine.py` (American odds, EV, no-vig)
6. `src/observability/journal.py` (`predicted_home_win` = edge-selected side, not raw favorite — normalize in analytics layer)
7. `src/experiments/prospective_diagnostic.py` (reuse `_ListStore`, join keys)
8. `src/app/consensus_strategy.py` (consensus-confirmed policy family C)

## Key semantics

```text
model_probability     = P(home wins)
market_probability    = no-vig P(home wins)
edge                  = model_probability - market_probability (home-relative)
raw_model_favorite    = HOME if model_probability >= 0.5 else AWAY
market_favorite       = HOME if market_probability >= 0.5 else AWAY
edge_selected_side    = HOME if edge >= 0 else AWAY
crossover             = edge_selected_side != raw_model_favorite
baseline PLAY         = abs(edge) >= 0.02
```

Journal join key: `(game_pk, prediction_timestamp)` via `board._prediction_key`.

Dedup: latest prediction per `game_pk` before first pitch (match board loader).

## Reuse before rewrite

- `load_daily_board_with_diagnostics` for board-shaped rows
- `market.engine.american_to_decimal`, `expected_value` for flat 1u ROI
- `consensus_strategy.consensus_confirmed_play` as reference for family C (may move logic into `play_policy.py` cleanly)

## Policy families to implement (fixed grid)

- **A** Baseline: `abs(edge) >= t` for t in {0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05, 0.06}
- **B** No crossover: A + edge side == raw model favorite
- **C** Consensus: model fav == market fav + confidence floors {0.52, 0.54, 0.55, 0.56, 0.58, 0.60}
- **D** Consensus + bounded edge ranges (modest grid per task brief)
- **E** No crossover + upper disagreement cap
- **F** Confidence-first (document tie semantics at 0.5)

Bounded ranges (pp): (1–4), (1–5), (1–6), (1–8), (2–4), (2–5), (2–6), (2–8), (3–5), (3–6), (3–8)

## Walk-forward

Chronological splits over resolved games ordered by `game_start_timestamp` or
`prediction_timestamp`. With ~278 games, use 3–4 folds max; hold out final
20–25% untouched for confirmatory evaluation. Document small-N limitation.

Primary selection metric order: **ROI → units → sample size → fold stability**.
Win rate is secondary.

## Reason codes (play_policy.py)

`MODEL_MARKET_AGREE`, `MODEL_CONFIDENCE_ABOVE_FLOOR`, `EDGE_WITHIN_VALIDATED_RANGE`,
`CROSSOVER_BLOCKED`, `LARGE_DISAGREEMENT_BLOCKED`, `EDGE_BELOW_THRESHOLD`,
`EDGE_ABOVE_CAP`, `MODEL_CONFIDENCE_TOO_LOW`, `MISSING_ODDS`, etc.

## ROI flat 1u

Win: profit = decimal_odds - 1 (or American equivalent). Loss: -1. Pending: exclude from denominator.

## Tests to add

`tests/unit/market/test_play_policy.py`, `tests/unit/market/test_policy_evaluation.py`

Cover: dedup, side semantics, threshold boundaries (fp-safe), bounded edges,
consensus ties, ROI American +/- , walk-forward no leakage, empty samples.

## Commands

```bash
.venv/bin/python scripts/market_policy_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --run-id market-006

.venv/bin/python -m pytest tests/unit/market/ -q
git diff --check
```

## Deliverables

- `src/market/play_policy.py`
- `src/market/policy_evaluation.py`
- `scripts/market_policy_study.py`
- `reports/market/play-policy-study-market-006.json` + `.md`
- Draft `docs/decisions/ADR-007-play-staking-policy.md` (PROPOSED status)
- Task handoff in `tasks/MARKET-006-play-policy-study.md`

## Valid conclusions

- `POLICY CANDIDATE READY FOR PROSPECTIVE SHADOW VALIDATION`
- `POLICY CANDIDATE READY FOR ADR REVIEW`
- **`NO POLICY READY FOR PRODUCTION`** ← valid success
