# ML-010 - 2026 Final Holdout Evaluation

## Status

done

## Dependencies

- ML-009

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Evaluate the locked V1 methodology on the untouched 2026 final holdout exactly
once, preserving traceability and preventing post-hoc methodology changes.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `tasks/ML-009-methodology-lock.md`

## Inputs

- Locked ML-009 methodology from ADR-006.
- Certified 2021-2025 development build.
- 2026 holdout data, ingested and certified under the same point-in-time and
  leakage rules before evaluation.

## Outputs

- Final holdout evaluation report with log loss, Brier score, calibration,
  ROC-AUC, accuracy, and secondary market-relative metrics where valid.
- Durable prediction table keyed by `game_pk`.
- Updated `state/CURRENT.md` and task handoff.

## Critical constraints

- No model/window/hyperparameter/calibration selection after seeing 2026.
- Fit preprocessing only on the locked training partition.
- Preserve chronology and train/test separation.
- Do not use future/closing odds as pregame inputs.
- Historical opening-market ROI, if reported, must be labeled as simulated ROI
  at opening prices and remain secondary.

## Acceptance criteria

- Evaluation uses only the locked ML-009 methodology.
- 2026 metrics and predictions are reproducible.
- Holdout report traces to dataset certification, Gold feature build, code
  version, and methodology decision.
- Review and tester gates pass with no P0/P1 findings.

## Required tests

- Integration test or regression check proving the holdout runner refuses to run
  without a locked methodology decision.
- Chronology/train-test overlap checks.
- Preprocessing-fit isolation checks.

## Merge-blocking conditions

- Any post-holdout methodology change.
- Any 2026 leakage into development selection.
- Missing traceability from final metrics to data/model/code versions.

## Handoff

Record final holdout metrics, artifact paths, commands run, tests run, pass/fail
status, and whether downstream product/dashboard tasks are ready.
