# DATA-004 — Normalized Game and Odds Datasets

## Status

blocked

## Dependencies

- DATA-002
- DATA-003

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create stable Silver-layer normalized datasets and the MLB↔odds mapping contract.

## Allowed files

- `src/transforms/`
- `tests/unit/transforms/`
- `tests/integration/transforms/`

## Required outputs

At minimum:

- games,
- team-game statistics,
- pitcher-game/appearance statistics,
- odds snapshots,
- mapping between source odds event and MLB `game_pk`.

## Critical constraints

- joins have explicit cardinality assertions,
- one game is not identified by team/date alone,
- no silent many-to-many joins,
- source timestamps preserved.

## Acceptance criteria

- deterministic normalized tables,
- unique keys documented/tested,
- doubleheaders map correctly,
- unmapped odds events are surfaced rather than silently attached.
