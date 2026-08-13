# ML-011 - model diagnostics report

## Status

Completed.

## Scope

Generate an artifact-backed V1 diagnostic report to support underfit/overfit discussion without retraining, retuning, or re-evaluating 2026.

## Outcome

- Added `scripts/model_diagnostics.py`.
- Generated `reports/experiments/v1-model-diagnostics.json` from existing repaired tuning and final holdout reports.
- Report includes development-vs-holdout metric gaps, fold stability, calibration variants, holdout confidence distribution, and interpretation caveats.

## Constraints

- No methodology changes.
- No new holdout evaluation.
- No model retraining.
