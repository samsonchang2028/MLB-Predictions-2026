# MARKET-006 PLAY Policy Study (market-006)

Generated: 2026-09-09T04:53:09.600285+00:00

## Population

- Resolved latest-per-game rows: 278
- Malformed daily skips: 0

journal.predicted_home_win records whether the edge-selected side won, not the raw model favorite. Analytics normalize on read; immutable history is never rewritten.

## Baseline (abs(edge) >= 0.02)

- Full-sample ROI: -0.14475435893438993
- Full-sample units: -28.371854351140428
- Full-sample resolved plays: 196
- Holdout ROI: -0.22483254260305213
- Holdout units: -9.667799331931242

## Overfitting guard

Ranking and gate selection use development folds only. Full-sample and holdout bests are reported separately to avoid conflation.

**full-sample in-sample (not used for selection):**
- `no_crossover_cap_0.04_min_0.005` — roi=0.05409293640248045, dev_mean_roi=-0.011292328136137675, holdout_roi=0.2660134911639056, n_holdout=17

**development-fold mean ROI (used for candidate ranking):**
- `no_crossover_cap_0.05_min_0.040` — roi=-0.10181412443775702, dev_mean_roi=0.29999421672811405, holdout_roi=-0.35514018691588783, n_holdout=3

**untouched chronological holdout block (not used for ranking):**
- `no_crossover_cap_0.04_min_0.005` — roi=0.05409293640248045, dev_mean_roi=-0.011292328136137675, holdout_roi=0.2660134911639056, n_holdout=17

## Top candidates (ranked by dev-fold mean ROI only)

| rank | policy | family | dev_mean_roi | dev_n | holdout_roi | holdout_units | n_holdout |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | no_crossover_cap_0.05_min_0.040 | E | 0.29999421672811405 | 8 | -0.35514018691588783 | -1.0654205607476634 | 3 |
| 2 | no_crossover_cap_0.05_min_0.030 | E | 0.23647820721176474 | 22 | -0.19042959734523285 | -1.33300718141663 | 7 |
| 3 | no_crossover_cap_0.05_min_0.035 | E | 0.15727376869283918 | 15 | -0.2435925867258039 | -1.2179629336290194 | 5 |
| 4 | no_crossover_cap_0.08_min_0.060 | E | 0.0629944300525264 | 20 | -0.6473282442748092 | -3.236641221374046 | 5 |
| 5 | consensus_bounded_0.03_0.05_floor_0.60 | D | 0.04423766331620077 | 11 | -1.0 | -1.0 | 1 |
| 6 | consensus_bounded_0.03_0.08_floor_0.55 | D | 0.016800367547756146 | 47 | -0.08363295147105648 | -1.0035954176526778 | 12 |
| 7 | consensus_bounded_0.03_0.08_floor_0.52 | D | 0.006607828398232512 | 59 | -0.03604033222395757 | -0.5406049833593636 | 15 |
| 8 | consensus_bounded_0.03_0.08_floor_0.56 | D | 0.005051957653442902 | 42 | -0.16827754952466561 | -1.8510530447713218 | 11 |
| 9 | consensus_bounded_0.03_0.06_floor_0.55 | D | 0.004042005650622749 | 31 | 0.126024087248416 | 0.882168610738912 | 7 |
| 10 | no_crossover_abs_edge_ge_0.060 | B | 0.0026335084289947955 | 36 | -0.35958322939014453 | -3.2362490645113007 | 9 |

## Production verdict

**NO POLICY READY FOR PRODUCTION**

No fixed-grid candidate passed all production gates on walk-forward holdout.

Shadow candidate: None

