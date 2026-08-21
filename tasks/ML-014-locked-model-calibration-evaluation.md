# ML-014 — Locked Model Calibration Evaluation

## Status

candidate

## Dependencies

- ML-009

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Determine, using pre-2026 temporal evidence only, whether Platt/sigmoid or
isotonic calibration improves the ADR-006 tuned XGBoost model.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `tasks/ML-008-calibration.md`
- `tasks/ML-009-methodology-lock.md`

## Allowed files

- `src/evaluation/calibration.py`
- `scripts/evaluate_locked_calibration.py`
- `tests/unit/evaluation/test_calibration.py`
- `tests/leakage/test_calibration_leakage.py`
- `reports/experiments/`
- this task file
- `state/CURRENT.md`

## Do not modify

- feature definitions
- model hyperparameters
- deployment, API, Streamlit, homelab, simulation, or market code
- the 2026 holdout artifact

## Inputs

- repaired certified 2021-2025 build `a910017bac839af5`
- ADR-006 locked XGBoost hyperparameters
- ADR-003 expanding development folds

## Outputs

- reproducible raw vs Platt vs isotonic evaluation artifact
- reliability buckets and fold-stability diagnostics
- exactly one evidence recommendation, without automatic production promotion

## Requirements

- score all three methods from the same full-training-fold base-model probabilities
- fit each calibrator only on chronological inner-training predictions
- report log loss, Brier, ECE, reliability buckets, and fold stability
- preserve calibrator predictions across serialization/loading

## Critical correctness constraints

- 2026 must not influence calibration-method selection
- no evaluation label may fit the base model or calibrator
- all training observations must precede their evaluation fold
- calibrated probabilities must remain in `[0, 1]`
- ADR-006 remains the production lock unless a later accepted decision authorizes promotion

## Acceptance criteria

- raw, Platt, and isotonic use identical evaluation rows and refit base probabilities
- the report identifies every fold's train seasons, calibration partition, and test season
- diagnostics include sample size and observed win rate per probability bucket
- recommendation is one of KEEP RAW, USE PLATT, USE ISOTONIC, or INCONCLUSIVE

## Required tests

- calibration/evaluation-label isolation
- chronological partition ordering
- explicit 2026 exclusion
- probability bounds
- serialization round trip
- existing leakage suite

## Handoff

Record files changed, methodology, metrics, fold results, tests, leakage status,
recommendation, and whether production promotion is authorized.

## Candidate handoff

- Added a refit-base comparison in which raw, sigmoid, and isotonic score the
  same full-training-fold XGBoost probabilities. Calibrators are fitted only
  from the later 20% of each fold's training rows using predictions from a base
  model fitted on the earlier 80%.
- Evidence artifact:
  `reports/experiments/v1-locked-calibration-pre2026-a910017bac839af5.json`.
- Aggregate results over 9,694 development predictions:
  - raw: log loss 0.681237, Brier 0.244084, ECE 0.015782;
  - sigmoid: log loss 0.680960, Brier 0.243985, ECE 0.006296;
  - isotonic: log loss 0.697098, Brier 0.245104, ECE 0.018274.
- Sigmoid improved log loss in only 2/4 temporal folds and worsened both log
  loss and Brier in 2023 and 2024. Isotonic improved log loss in only 1/4.
- Recommendation: **KEEP RAW**. The small pooled sigmoid gain is not temporally
  consistent enough to justify changing the locked method.
- 2026 was not loaded, scored, trained on, or used for selection.
- Tests: `python -m pytest tests/leakage tests/unit/evaluation -q` -> 96 passed.
- Production promotion is not authorized; ADR-006 remains unchanged.
- Tester gate: **PASS** (2026-08-21; 60 evaluation + 42 leakage + 947 full suite).
- Reviewer gate: pending.
