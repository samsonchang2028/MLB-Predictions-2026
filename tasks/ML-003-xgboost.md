# ML-003 — XGBoost Model

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

Build an XGBoost probabilistic classifier as the primary nonlinear candidate.

## Allowed files

- `src/models/xgboost_model.py`
- `tests/unit/models/test_xgboost.py`

## Constraints

- do not overbuild tuning infrastructure yet,
- deterministic/reproducible configuration where practical,
- keep probability output as the core contract.

## Acceptance criteria

- fits and predicts probabilities,
- model metadata retained,
- tests pass.
