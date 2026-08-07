# FEAT-004 — Game Feature Matrix

## Status

blocked

## Dependencies

- FEAT-001
- FEAT-002
- FEAT-003

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Combine component features into one game-level Gold dataset for training and inference.

## Allowed files

- `src/features/build.py`
- `tests/unit/features/test_build.py`
- `tests/integration/features/`
- `tests/leakage/test_feature_matrix.py`

## Requirements

- one row per MLB game,
- home and away component features,
- explicit differential features,
- target stored separately from predictive feature list,
- prediction/reference timestamp retained.

## Critical constraints

- no many-to-many joins,
- no target columns in model feature list,
- all feature timestamps valid for the prediction time.

## Acceptance criteria

- uniqueness test passes,
- schema is deterministic,
- leakage checks pass,
- target isolation test passes.
