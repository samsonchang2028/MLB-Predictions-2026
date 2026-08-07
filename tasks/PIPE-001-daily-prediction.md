# PIPE-001 — Daily Prediction Pipeline

## Status

blocked

## Dependencies

- MARKET-001

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create one deterministic daily pipeline that builds today's point-in-time features and emits immutable predictions.

## Requirements

- today's schedule,
- probable starters,
- current source data refresh,
- current odds snapshot,
- feature generation,
- model loading,
- probability prediction,
- market comparison,
- immutable prediction record.

## Critical constraints

Each saved prediction must include:

- game_pk,
- prediction timestamp,
- model version,
- feature/schema version if available,
- model probability,
- odds snapshot timestamp,
- market probability,
- edge.
