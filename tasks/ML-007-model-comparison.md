# ML-007 — Model and Window Comparison

## Status

blocked

## Dependencies

- ML-005
- ML-006

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Compare model family × training-window strategy using development folds only.

## Primary criteria

1. log loss,
2. Brier score,
3. calibration behavior.

## Secondary

- ROC-AUC,
- accuracy,
- market-relative metrics if available,
- simulated ROI.

## Critical constraint

Do not select the winner using 2026.
