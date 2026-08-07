# ML-004 — Walk-Forward Validation Framework

## Status

blocked

## Dependencies

- ML-001
- ML-002
- ML-003

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create reusable chronological evaluation infrastructure shared by all model families.

## Allowed files

- `src/evaluation/splits.py`
- `src/evaluation/runner.py`
- `tests/unit/evaluation/`
- `tests/leakage/test_split_leakage.py`

## Critical constraints

- no random primary split,
- no overlap between train and test,
- 2026 excluded from development folds,
- preprocessing fit inside each fold.

## Acceptance criteria

- deterministic fold definitions,
- overlap assertions,
- chronological assertions,
- same evaluator works for all three model families.
