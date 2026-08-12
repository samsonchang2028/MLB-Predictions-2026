# ML-009 - Methodology Lock

## Status

ready

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
