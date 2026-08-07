# DATA-002 - MLB Schedule Historical Ingestion

## Status

done

## Dependencies

- DATA-001

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Ingest historical MLB schedules/game records while preserving immutable raw API
payloads and stable game identity.

Scope note: this completed task covers schedule/game records only.
Appearance-capable game-detail, boxscore, starter, and pitcher appearance
ingestion is owned by DATA-005.

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

## Completion handoff

- Added fixture-driven season and inclusive date-range MLB schedule ingestion with exact immutable raw-byte retention.
- Canonical records use `game_pk`; doubleheaders and full status/reschedule history remain distinct and deterministic.
- Strict schema validation, stale-update protection, timestamped fetch/observation lineage, historical equal-time conflict rejection, and transaction rollback are covered.
- `python -m pytest -q`: 38 passed; focused, compile, and diff checks passed.
- Reviewer approved and Tester passed after two repair loops; no open P0/P1 or leakage finding.
- HTTP transport remains an explicit injected `fetch_schedule(params) -> bytes` boundary; no speculative client dependency was added.
- No ADR change required; project state updated to unlock DATA-004.
