# ML-008 — Probability Calibration

## Status

blocked

## Dependencies

- ML-007

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Evaluate whether the selected model/window benefits from probability calibration.

## Candidate methods

- Platt/sigmoid,
- isotonic.

## Critical constraints

- calibration data must not overlap the final evaluation partition,
- compare calibrated vs uncalibrated log loss/Brier/reliability,
- do not calibrate on 2026 during model development.
