# ADR-003: Temporal Validation Strategy

## Status

Accepted.

## Context

Random train/test splits do not simulate deployment and can allow newer baseball regimes to influence evaluation of older games.

## Decision

V1 compares:

### Expanding window

- train 2021 → test 2022
- train 2021-2022 → test 2023
- train 2021-2023 → test 2024
- train 2021-2024 → test 2025

### Rolling recent windows

Test both:

- rolling 2-season history,
- rolling 3-season history.

### Final holdout

2026 remains untouched during model/window/hyperparameter selection.

After methodology is locked:

- train according to the selected strategy using data through 2025,
- evaluate on 2026.

## Metrics

Primary:

- log loss,
- Brier score,
- calibration.

Secondary:

- ROC-AUC,
- accuracy,
- simulated ROI,
- market-relative performance.

2026 remains excluded from feature methodology selection, hyperparameter/model
selection, training-window selection, calibration-method selection, and market
strategy selection.

Historical data certification and leakage checks are merge-blocking
prerequisites for downstream model work.
