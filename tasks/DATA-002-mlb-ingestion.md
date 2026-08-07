# DATA-002 — MLB Historical Ingestion

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

Ingest historical MLB schedules/game records while preserving immutable raw API payloads and stable game identity.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`

## Allowed files

- `src/ingestion/mlb/`
- `tests/unit/ingestion/mlb/`
- `tests/integration/ingestion/mlb/`

## Critical constraints

- use `game_pk` as canonical MLB game identifier,
- ingestion is idempotent,
- preserve raw responses,
- do not collapse doubleheaders,
- do not silently discard postponed/rescheduled/suspended states.

## Acceptance criteria

- season/date-range ingestion supported,
- duplicate ingestion does not duplicate canonical records,
- raw payloads retained,
- game status and timestamps retained,
- tests cover doubleheaders and reschedules.
