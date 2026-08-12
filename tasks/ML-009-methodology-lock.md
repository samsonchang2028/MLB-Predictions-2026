# ML-009 - Methodology Lock

## Status

done

## Dependencies

- ML-008
- FEAT-006

## Execution

Primary role: orchestrator
Review required: yes
Tester required: no
Worktree required: no

## Goal

Review the repaired 2021-2025 experiment evidence and lock the V1 model,
training-window, and calibration methodology before any 2026 holdout evaluation.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-005-certification-defense-in-depth.md`
- `reports/experiments/v1-repaired-a910017bac839af5.json`
- `reports/experiments/v1-repaired-xgboost-tuning-a910017bac839af5.json`
- `reports/data-quality/gold-completeness-a910017bac839af5.json`

## Inputs

- Certified repaired historical build `a910017bac839af5`.
- Gold completeness PASS report.
- Leakage PASS evidence.
- Repaired model/window comparison evidence.
- XGBoost tuning evidence.
- Calibration evidence.

## Outputs

- Accepted ADR or decision document that explicitly locks:
  - model family,
  - hyperparameters,
  - training-window strategy,
  - calibration choice,
  - selection rationale and metric priority,
  - certified dataset/build identity used for selection.
- Updated `state/CURRENT.md` and `tasks/index.md`.

## Critical constraints

- Do not inspect or evaluate 2026.
- Do not use simulated ROI as the primary selection criterion.
- Do not use the invalid first real experiment as methodology-selection evidence;
  it is diagnostic only because pitching signal was absent.
- If methodology is not locked, document exactly what additional 2021-2025-only
  evidence is required.

## Acceptance criteria

- Methodology decision is explicit: `LOCKED` or `NOT LOCKED`.
- If locked, ML-010 becomes ready.
- If not locked, a narrower follow-up task is created and ML-010 remains blocked.
- The decision cites log loss, Brier score, calibration/reliability, and relevant
  secondary metrics.

## Required checks

- Documentation/path sanity.
- `git diff --check`.

## Merge-blocking conditions

- Any 2026 data is inspected before lock.
- The decision relies primarily on ROI or accuracy.
- The selected methodology cannot be traced to the repaired certified build.

## Handoff

Record methodology status, selected model/window/calibration if locked, evidence
files reviewed, commands/checks run, and whether ML-010 is ready.

## Completion handoff

- Methodology status: LOCKED.
- Added ADR-006 selecting tuned shallow XGBoost + expanding window +
  uncalibrated probabilities on repaired certified build `a910017bac839af5`.
- Selected hyperparameters:
  `max_depth=2`, `learning_rate=0.03`, `n_estimators=300`, `reg_lambda=10.0`,
  `min_child_weight=3.0`, `subsample=0.8`, `colsample_bytree=0.8`.
- Evidence reviewed:
  `reports/experiments/v1-repaired-a910017bac839af5.json`,
  `reports/experiments/v1-repaired-xgboost-tuning-a910017bac839af5.json`,
  `reports/data-quality/gold-completeness-a910017bac839af5.json`,
  ADR-003, and ADR-005.
- 2026 was not inspected.
- Reviewer verdict: APPROVE, no P0/P1 findings. One P2 stale roadmap ready-batch
  guidance finding was repaired before commit.
- ML-010 is now ready for implementation/review/test gates.
