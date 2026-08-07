# DATA-003 - Odds Snapshot Ingestion

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

Ingest MLB moneyline snapshots with bookmaker/source and observation timestamps.

Scope note: this completed task covers live/future timestamped odds snapshots.
The finalized historical odds archive is a separate source and methodology owned
by DATA-008 and DATA-009.

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

Do not overwrite stored timestamped snapshots with newer prices.

## Acceptance criteria

- repeated identical snapshots are handled deterministically,
- multiple books and multiple timestamps can coexist,
- malformed/missing timestamp data fails clearly,
- tests pass.

## Completion handoff

- Added fixture-driven The Odds API moneyline parsing and append-only DuckDB ingestion with immutable-key conflict detection.
- Preserves source/event/book/outcome/price plus timezone-aware snapshot and commence instants; supports multiple books and timestamps.
- Added explicit transaction rollback, signed-32-bit American-price validation, and the minimal `pytz` dependency required for DuckDB `TIMESTAMPTZ` retrieval.
- `python -m pytest`: 31 passed; compile, dependency, and diff checks passed.
- Reviewer approved and Tester passed after one repair loop; no open P0/P1 or leakage finding.
- Optional P3: commit a regression that forces a database constraint failure after an earlier row insert; the adversarial gate manually verified rollback.
- No ADR change required; DATA-004 remains blocked on DATA-002.
