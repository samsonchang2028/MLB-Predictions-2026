# APP-002 - Performance Dashboard

## Status

done

## Dependencies

- APP-001
- OBS-001

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Expose historical prediction quality and experiment results.

## Requirements

Display:

- log loss,
- Brier score,
- calibration/reliability view,
- model/window comparisons,
- prediction history,
- market-relative metrics where available,
- simulated ROI as secondary context.

Optional fun side page may track the user's and friend's promotional-dollar
challenge, but it must remain separate from canonical model evaluation.

## Current readiness note

APP-002 is dependency-ready because APP-001 and OBS-001 are merged. If it is
implemented before ML-009/ML-010, it must label repaired 2021-2025 experiment
results as development evidence and must not imply 2026 final-holdout results
exist.

## Critical constraints

- Do not recompute model, market, or journal metrics inside Streamlit page code.
- Do not present simulated ROI as primary model quality.
- Do not inspect or display 2026 holdout results unless ML-010 has completed.
