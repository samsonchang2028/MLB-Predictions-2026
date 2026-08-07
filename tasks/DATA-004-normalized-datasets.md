# DATA-004 — Normalized Game and Odds Datasets

## Status

candidate

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

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`

## Inputs

- Bronze MLB schedule tables produced by DATA-002.
- Bronze odds snapshot tables produced by DATA-003.
- Source payload, observation, and commence timestamps retained by ingestion.

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

## Required tests

- deterministic repeated normalization,
- unique-key and join-cardinality assertions,
- doubleheader mapping,
- unmapped and ambiguous odds-event handling,
- source-timestamp preservation.

## Handoff

Document normalized table keys, mapping cardinalities, commands run, gate results, and any unmapped/ambiguous-event limitations.
