# MARKET-008 CLV Validation (market-009-rerun)

Generated: 2026-09-11T00:50:12.889717+00:00

## Closing-line definitions

- **Strict (primary):** odds_closes.jsonl snapshot for (run_date, game_pk, bookmaker, prediction_timestamp) where prediction_timestamp < snapshot_timestamp <= game_start_timestamp; falls back to odds_books.jsonl when no odds_closes row matches.
- **Pregame latest (secondary):** Latest snapshot for source book where snapshot_timestamp <= game_start_timestamp (may equal prediction-time line -> CLV = 0).

## Population

- Latest-per-game rows: 315
- Malformed daily skips: 0
- Journal side mismatches: 0
- Prediction anchor: earliest prediction per game_pk (retrospective CLV population)

journal.predicted_home_win records whether the edge-selected side won, not the raw model favorite. Analytics normalize on read; immutable history is never rewritten.

## Aggregate CLV (strict close)

- CLV N: 113
- Mean CLV (pp): 0.0010124469792508546
- Median CLV (pp): 0.0
- Positive CLV rate: 0.39823008849557523
- Outcome N (same strict population): 112
- Outcome ROI (strict population): -0.10159755815676966

## Aggregate CLV (pregame latest)

- CLV N: 171
- Mean CLV (pp): 0.0006690439102651847

## Outcome / ROI (all latest-per-game, resolved only)

- Outcome N: 308
- Pending excluded: 7
- ROI: -0.04419819104961659
- Units: -13.61304284328191

## Strict-close rejection summary

- close_after_first_pitch: 143
- close_at_or_before_prediction: 58
- missing_bookmaker_row: 1

## Verdict

**POSITIVE CLV WITH NEGATIVE ROI** (case 2)

Closing line moved favorably but realized ROI remains negative.

ADR-007 recommendation: Edge may contain information but PLAY policy is not validated; do not accept ADR-007 or change production PLAY threshold.

ADR-007 status: PROPOSED — not accepted; observability only

## Strict-close slices (coarse edge buckets, pp)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| 0-2pp | 35 | 0.001467025228741614 | 35 | 0.06658815536283359 |
| 2-4pp | 21 | 0.0015869742974930542 | 21 | 0.15017904159068882 |
| 4-6pp | 18 | -0.0004345210807466989 | 18 | -0.3103279941213947 |
| 6-8pp | 17 | 0.003947265932619211 | 17 | -0.5284290503766274 |
| 8pp+ | 22 | -0.0013430898181386395 | 21 | -0.10924162257495587 |

## Strict-close slices (crossover)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| crossover | 58 | 0.002814166994499143 | 58 | -0.13137931034482758 |
| no_crossover | 55 | -0.0008875486731927951 | 54 | -0.06960975025107784 |

## Strict-close slices (model/market agreement)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| model_market_agree | 95 | 0.0009531244004268197 | 94 | -0.09340213085070181 |
| model_market_disagree | 18 | 0.0013255383674888167 | 18 | -0.144395900755124 |

## Strict-close slices (selected side)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| AWAY | 87 | 0.0002314974516027699 | 86 | -0.05135511218796634 |
| HOME | 26 | 0.0036256242448425226 | 26 | -0.2677841102074268 |

## Strict-close slices (market favorite role)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| market_favorite | 30 | -0.0022151381455451703 | 30 | -0.0318512790311133 |
| market_underdog | 70 | 0.002638957443268811 | 70 | -0.08842857142857141 |
| near_even | 13 | -0.0002974129236242381 | 12 | -0.3527823452187337 |

## Strict-close slices (lead time)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| 2_6h | 23 | -0.00044918800703922803 | 23 | 0.06811189955159574 |
| 6_24h | 90 | 0.0013859759201916536 | 89 | -0.14545505846342588 |

## Strict-close slices (bookmaker)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| draftkings | 113 | 0.0010124469792508546 | 112 | -0.10159755815676966 |

