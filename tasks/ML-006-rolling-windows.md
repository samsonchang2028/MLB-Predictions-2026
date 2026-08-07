# ML-006 — Rolling Recent-Window Experiments

## Status

blocked

## Dependencies

- ML-004

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Compare recent-history training windows against expanding history.

## Strategies

- rolling 2-season,
- rolling 3-season.

## Requirements

Use only folds with sufficient prior seasons.

Store model/window/fold metadata alongside predictions.

Do not inspect 2026.
