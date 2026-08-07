# DATA-003 — Odds Snapshot Ingestion

## Status

blocked

## Dependencies

- DATA-001

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Ingest MLB moneyline snapshots with bookmaker/source and observation timestamps.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`

## Allowed files

- `src/ingestion/odds/`
- `tests/unit/ingestion/odds/`
- `tests/integration/ingestion/odds/`

## Critical constraints

Every observation must preserve:

- event/source identifier,
- bookmaker,
- home/away outcome,
- American price,
- snapshot timestamp,
- commence time.

Do not overwrite historical snapshots with newer prices.

## Acceptance criteria

- repeated identical snapshots are handled deterministically,
- multiple books and multiple timestamps can coexist,
- malformed/missing timestamp data fails clearly,
- tests pass.
