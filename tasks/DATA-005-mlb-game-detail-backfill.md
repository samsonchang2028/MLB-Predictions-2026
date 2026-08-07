# DATA-005 - MLB Historical Game Detail Backfill

## Status

ready

## Dependencies

- DATA-004

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Backfill immutable, restartable MLB game-detail data for 2021-2025 so starter
and bullpen features have real pitcher appearance inputs.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `src/ingestion/mlb/schedule.py`
- `src/transforms/silver.py`

## Allowed files

- `src/ingestion/mlb/`
- `src/transforms/`
- `tests/unit/ingestion/mlb/`
- `tests/integration/ingestion/mlb/`
- `tests/unit/transforms/`
- `tests/integration/transforms/`

## Inputs

- Existing Bronze/Silver schedule data keyed by `game_pk`.
- MLB Stats API game-detail or boxscore-style responses needed for V1 pitcher
  starters and appearances.

## Outputs

- Immutable Bronze game-detail raw payloads and provenance.
- Bronze tables/indexes sufficient to identify fetched, missing, and failed
  `game_pk` records.
- Populated Silver pitcher appearance/starter contract sufficient for FEAT-002
  and FEAT-003.

## Requirements

- Cover 2021-2025 historical games.
- Use MLB `game_pk` as the canonical key.
- Preserve source, endpoint, request parameters, retrieval timestamp,
  `game_pk`, payload hash/checksum, and ingestion run/build identity.
- Design the backfill as idempotent and restartable by `game_pk` or small
  batches, not one fragile all-seasons script.
- Never silently overwrite valid existing Bronze payloads.
- Make missing/failed games queryable and explicitly retryable.
- Prefer the smallest MLB endpoint set capable of supporting V1 starter and
  bullpen features.
- Do not add Statcast or pitch-level data unless a current V1 requirement
  proves it is necessary.

## Critical correctness constraints

- Do not infer pitcher appearances from schedule-only data.
- Preserve actual starter identity separately from probable starter when both
  are available.
- Preserve appearance order and team identity.
- Do not silently drop postponed, suspended, cancelled, doubleheader, or
  rescheduled games.

## Acceptance criteria

- Re-running the same backfill does not duplicate canonical rows.
- Existing valid raw payloads are retained and hash-checked.
- Failed/missing games are visible and retryable.
- Silver pitcher appearances are populated from appearance-capable data.
- Doubleheaders and same-day games between the same teams remain distinct by
  `game_pk`.

## Required tests

- Unit tests for payload parsing and pitcher appearance extraction.
- Integration tests for idempotent backfill/retry behavior.
- Tests for doubleheaders, postponed/rescheduled/suspended/cancelled games
  where fixtures are available.
- Tests for starter changes, missing probable starters, actual starters, and
  bullpen appearances.
- Regression tests proving raw Bronze payloads are not overwritten silently.

## Merge-blocking conditions

- Any silent raw overwrite.
- Any `game_pk` identity loss.
- Any fixture where a same-day doubleheader collapses into one record.
- Pitcher appearance rows that cannot be traced to a raw source payload.

## Handoff

Record endpoint choices, backfill restart semantics, Bronze/Silver tables,
coverage counts, failed/missing game reporting, commands run, test results, and
any data cases deferred to DATA-006 validation.
