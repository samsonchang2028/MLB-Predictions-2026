# OBS-001 — Prediction Journal

## Status

blocked

## Dependencies

- PIPE-001

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Persist every generated prediction and later attach game result/evaluation without mutating original prediction fields.

## Critical constraints

- original prediction values are immutable,
- result enrichment is separate,
- model version preserved,
- paper/backtest metrics remain separate from any optional side-game tracking.
