# ADR-006: V1 Model Methodology Lock

## Status

Accepted.

## Context

The repaired 2021-2025 historical dataset build `a910017bac839af5` has passed:

- data certification: `state/data-certifications/certification-PASS-a910017bac839af5.json`,
- Gold feature completeness: `reports/data-quality/gold-completeness-a910017bac839af5.json`,
- leakage checks recorded in `state/CURRENT.md`.

The first real experiment on build `7225f7f46a5e27e9` is diagnostic only and not
valid for methodology selection, because the pitching-stat projection defect left
starter/bullpen signal absent. ADR-005 requires methodology selection to use the
repaired build evidence.

The repaired all-family comparison identified XGBoost with expanding windows as
the best repaired candidate by the project's primary ordering. A focused
20-candidate XGBoost tuning pass then improved the repaired expanding-window
evidence without inspecting 2026.

Evidence reviewed:

- `reports/experiments/v1-repaired-a910017bac839af5.json`
- `reports/experiments/v1-repaired-xgboost-tuning-a910017bac839af5.json`
- `reports/data-quality/gold-completeness-a910017bac839af5.json`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-005-certification-defense-in-depth.md`

## Decision

Lock the V1 methodology as:

- model family: XGBoost,
- training window: expanding,
- calibration: uncalibrated,
- selected hyperparameters:
  - `max_depth=2`,
  - `learning_rate=0.03`,
  - `n_estimators=300`,
  - `reg_lambda=10.0`,
  - `min_child_weight=3.0`,
  - `subsample=0.8`,
  - `colsample_bytree=0.8`.

Selection uses the repaired certified 2021-2025 build `a910017bac839af5`.
2026 was not inspected for this decision.

## Rationale

The tuned shallow XGBoost candidate had the best repaired expanding-fold primary
metrics in the tuning pass:

- log loss: `0.68124`,
- Brier score: `0.24408`,
- ECE: `0.01578`,
- ROC-AUC: `0.58446`,
- accuracy: `0.56396`,
- test games: `9,694`.

This improves materially over the untuned repaired XGBoost/expanding evidence
and keeps the selection aligned with ADR-003's primary probability-quality
metrics.

Calibration was reviewed but not selected. Sigmoid/Platt calibration improved
reliability on the fair base-fit comparison:

- tuned sigmoid ECE: `0.00582`,
- tuned sigmoid log loss: `0.68150`,
- tuned sigmoid Brier: `0.24425`.

However, the raw full-train tuned model remains better on the strict primary
log-loss/Brier ordering:

- raw full-train tuned log loss: `0.68124`,
- raw full-train tuned Brier: `0.24408`.

Because ADR-003 ranks log loss and Brier ahead of calibration behavior, the V1
lock uses the uncalibrated tuned model. The calibration evidence remains useful
context for later product display and post-V1 refinement.

## Consequences

ML-010 may evaluate the untouched 2026 final holdout exactly once using this
locked methodology.

After 2026 is evaluated, do not change the V1 model family, hyperparameters,
training-window strategy, or calibration choice based on the holdout result.
Any later methodology change must be treated as a post-V1 or V2 decision with a
new untouched evaluation policy.

Market evaluation remains secondary to probability quality. Historical archive
ROI, if reported, must remain labeled as simulated ROI at opening prices.
