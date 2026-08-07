# DATA-001 — Storage Foundation

## Status

blocked

## Dependencies

- META-001

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create the minimal V1 storage foundation for immutable raw data plus DuckDB/Parquet Bronze/Silver/Gold processing.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`

## Allowed files

- `src/storage/`
- `src/config/` if required
- `tests/unit/storage/`
- `tests/integration/storage/`
- minimal package/config files required to run tests

## Requirements

- initialize storage from an empty checkout,
- establish Bronze/Silver/Gold directories,
- provide DuckDB connection/schema initialization,
- initialization must be idempotent,
- keep implementation local/simple,
- do not add cloud infrastructure.

## Acceptance criteria

- repeated initialization produces the same valid state,
- a basic DuckDB query succeeds,
- directory structure is deterministic,
- tests pass.

## Required tests

- clean initialization,
- repeat initialization,
- database query smoke test.
