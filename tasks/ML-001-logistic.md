# ML-001 — Logistic Regression Baseline

## Status

blocked

## Dependencies

- FEAT-004

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Build a simple probabilistic Logistic Regression baseline.

## Allowed files

- `src/models/logistic.py`
- `tests/unit/models/test_logistic.py`

## Requirements

- predict `P(home_win)`,
- preprocessing contained in a train-only pipeline,
- deterministic seed/settings where applicable,
- expose model metadata.

## Acceptance criteria

- can fit/predict from Gold feature matrix,
- probabilities valid in [0,1],
- preprocessing leakage test passes.
