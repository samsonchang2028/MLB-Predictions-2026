# ADR-007: PLAY Staking Policy (Proposed)

## Status

PROPOSED — not accepted. Evidence from MARKET-006 (`reports/market/play-policy-study-market-006.json`)
does not support promoting any candidate from the fixed grid to production.

## Context

V1 ships a synthetic, display-only PLAY/PASS label in `src/app/board.py`
(`DEFAULT_EDGE_THRESHOLD = 0.02`, `abs(edge) >= threshold`). MARKET-001 computes
edge/EV; no staking ADR existed until this study. ML-015 found the locked
ADR-006 model discrimination intact while baseline PLAY underperformed on the
prospective journal.

MARKET-006 evaluated a fixed candidate grid (families A–F) over the canonical
latest-per-game resolved population with chronological walk-forward validation,
flat 1u ROI/units as primary metrics, and explicit production gates.

## Decision (proposed framework)

Until a candidate passes MARKET-006-style gates on an expanded prospective sample:

1. **Keep** `DEFAULT_EDGE_THRESHOLD = 0.02` unchanged for production display.
2. **Do not** treat dashboard PLAY labels as betting recommendations.
3. **Require** any future production PLAY policy to:
   - live in `src/market/play_policy.py` as deterministic pure functions,
   - emit structured reason codes,
   - be validated with `scripts/market_policy_study.py` (or successor) on the
     immutable journal,
   - pass walk-forward gates: holdout `n >= 30`, holdout ROI > 0, dev-mean ROI > 0,
     holdout units beat baseline, fold stability (majority of dev folds ROI >= 0),
   - receive explicit ADR acceptance before changing production behavior.

## Evidence summary (run `market-006`, N=278 resolved)

| Metric | Baseline 2% PLAY |
|---|---|
| Full-sample ROI | -14.5% |
| Full-sample units | -28.37u / 196 plays |
| Win rate | 40.3% |
| Holdout ROI | -22.5% |

Crossover baseline plays (edge-selected side ≠ raw model favorite): 86 plays,
33.7% win rate, -16.0% ROI — worse than no-crossover subset (110 plays, 43.6% win,
-13.3% ROI). Large-edge buckets remain weak, consistent with ML-015/ML-013.

Top dev-ranked candidate (`no_crossover_cap_0.04_min_0.005`, family E) showed
positive holdout ROI (+26.6%) but **failed** production gates (negative dev-mean
ROI, holdout n=17 < 30). **Verdict: NO POLICY READY FOR PRODUCTION.**

## Consequences

- MARKET-007 may expose baseline vs exploratory shadow policies with explicit
  NOT PRODUCTION labeling; no production PLAY change authorized.
- Further prospective data accumulation is required before ADR acceptance.
- Win rate alone must not drive policy selection; ROI, units, volume, and fold
  stability are primary.

## References

- `tasks/MARKET-006-play-policy-study.md`
- `docs/research/ml-015-prospective-model-market-diagnostic.md`
- `src/market/play_policy.py`, `src/market/policy_evaluation.py`
- `scripts/market_policy_study.py`
