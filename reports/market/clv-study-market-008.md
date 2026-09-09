# MARKET-008 CLV Validation (market-008)

Generated: 2026-09-09T20:50:17.912842+00:00

## Closing-line definitions

- **Strict (primary):** Latest odds_books.jsonl no-vig snapshot for the prediction source bookmaker on (game_pk, run_date) where prediction_timestamp < snapshot_timestamp <= game_start_timestamp.
- **Pregame latest (secondary):** Latest snapshot for source book where snapshot_timestamp <= game_start_timestamp (may equal prediction-time line -> CLV = 0).

## Population

- Latest-per-game rows: 310
- Malformed daily skips: 0
- Journal side mismatches: 0

journal.predicted_home_win records whether the edge-selected side won, not the raw model favorite. Analytics normalize on read; immutable history is never rewritten.

## Aggregate CLV (strict close)

- CLV N: 8
- Mean CLV (pp): 0.0024115350799655683
- Median CLV (pp): 0.0
- Positive CLV rate: 0.125
- Outcome N (same strict population): 8
- Outcome ROI (strict population): 0.12464912280701756

## Aggregate CLV (pregame latest)

- CLV N: 175
- Mean CLV (pp): 0.00011024160365556884

## Outcome / ROI (all latest-per-game, resolved only)

- Outcome N: 293
- Pending excluded: 17
- ROI: -0.04348977890179463
- Units: -12.742505218225828

## Strict-close rejection summary

- close_after_first_pitch: 134
- close_at_or_before_prediction: 167
- missing_bookmaker_row: 1

## Verdict

**CLV DATA INSUFFICIENT** (case 4)

Strict closing-line CLV sample size 8 is below the 30-game minimum required for evaluation.

ADR-007 recommendation: Do not accept ADR-007 or change production PLAY threshold. File prospective closing-odds capture (MARKET-009) before CLV can gate PLAY promotion.

ADR-007 status: PROPOSED — not accepted; observability only

## Strict-close slices (coarse edge buckets, pp)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| 0-2pp | 4 | -0.0005714519453524913 | 4 | 0.7017982456140351 |
| 2-4pp | 1 | -0.0022438490391759602 | 1 | -1.0 |
| 6-8pp | 1 | 0.0 | 1 | -1.0 |
| 8pp+ | 2 | 0.011910968730155236 | 2 | 0.09499999999999997 |

## Strict-close slices (crossover)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| crossover | 3 | -0.0007619359271366551 | 3 | 0.6433333333333334 |
| no_crossover | 5 | 0.0043156176842269025 | 5 | -0.18656140350877193 |

## Strict-close slices (model/market agreement)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| model_market_agree | 6 | -0.00038096796356832757 | 6 | 0.1345321637426901 |
| model_market_disagree | 2 | 0.010789044210567256 | 2 | 0.09499999999999997 |

## Strict-close slices (selected side)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| AWAY | 7 | 0.002756040091389221 | 7 | 0.017142857142857158 |
| HOME | 1 | 0.0 | 1 | 0.8771929824561404 |

## Strict-close slices (market favorite role)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| market_favorite | 3 | 0.0 | 3 | -0.3742690058479532 |
| market_underdog | 4 | 0.005394522105283628 | 4 | 0.78 |
| near_even | 1 | -0.0022858077814099653 | 1 | -1.0 |

## Strict-close slices (lead time)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| 2_6h | 3 | -0.0007619359271366551 | 3 | -0.2666666666666666 |
| 6_24h | 5 | 0.0043156176842269025 | 5 | 0.35943859649122806 |

## Strict-close slices (bookmaker)

| slice | clv_n | mean_clv | outcome_n | outcome_roi |
|---|---:|---:|---:|---:|
| draftkings | 8 | 0.0024115350799655683 | 8 | 0.12464912280701756 |

