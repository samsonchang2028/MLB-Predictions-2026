# ML-002 — Random Forest Baseline

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

Build a Random Forest probabilistic classifier for comparison with simpler and boosted models.

## Allowed files

- `src/models/random_forest.py`
- `tests/unit/models/test_random_forest.py`

## Constraints

Keep tuning minimal until the temporal validation framework exists.

## Acceptance criteria

- stable probability predictions,
- reproducible random seed,
- no custom framework abstraction added solely for this model.
