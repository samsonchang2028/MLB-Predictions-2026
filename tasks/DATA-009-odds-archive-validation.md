# DATA-009 - Historical Odds Archive Validation and Mapping Audit

## Status

blocked

## Dependencies

- DATA-004
- DATA-008

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Validate the historical odds archive and build an auditable mapping from archive
records to MLB `game_pk` without silently attaching ambiguous games.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `tasks/DATA-008-historical-odds-archive.md`
- `src/transforms/silver.py`

## Allowed files

- `src/validation/`
- `src/transforms/`
- `tests/unit/validation/`
- `tests/integration/validation/`
- `tests/unit/transforms/`
- `tests/integration/transforms/`
- `reports/data-quality/` if needed for coverage reports

## Inputs

- Parsed historical odds archive records from DATA-008.
- MLB schedule/Silver game candidates keyed by `game_pk`.

## Outputs

- Auditable odds-to-MLB mapping records with statuses:
  - `MATCHED`
  - `UNMATCHED`
  - `AMBIGUOUS`
- Coverage report by season/date/sportsbook.
- Validation results suitable for MARKET-001.

## Requirements

- Validate published SHA-256 match is recorded.
- Validate source file immutability.
- Validate moneylines parse correctly.
- Validate sportsbook identity is preserved.
- Validate opening odds are present/valid where expected.
- Validate home/away orientation.
- Validate deterministic team-name normalization.
- Validate every mapped odds record resolves to the proper MLB `game_pk`.
- Make unmatched odds records visible.
- Make ambiguous mappings visible.
- Forbid arbitrary "take first match" behavior.
- Handle doubleheaders explicitly.
- Report coverage by season/date/sportsbook.

## Mapping guidance

Use auditable inputs such as:

- game date,
- game/start time if available,
- home team,
- away team,
- MLB schedule candidates.

Because `game_pk` is not native to the odds archive, mapping must preserve
candidate counts and reasons. Ambiguous records must not enter canonical market
evaluation.

## Critical correctness constraints

- Same-day doubleheaders must not be attached by team/date alone.
- Ambiguous mappings must stay out of canonical opening-market evaluation.
- Historical opening-line benchmarks must be labeled as model edge versus
  opening market, not exact historical price at prediction time.

## Acceptance criteria

- Mapping results are deterministic.
- Each record has a mapping status and reason.
- `MATCHED` records have exactly one `game_pk`.
- `UNMATCHED` and `AMBIGUOUS` records have no canonical `game_pk` attachment.
- Coverage report exposes missingness and sportsbook/date gaps.

## Required tests

- Unit tests for team-name normalization and mapping decisions.
- Integration tests for matched, unmatched, and ambiguous records.
- Doubleheader and same-day same-team matchup tests.
- Home/away swapped orientation regression test.
- Coverage-report test by season/date/sportsbook.

## Merge-blocking conditions

- Any arbitrary first-candidate attachment.
- Any ambiguous doubleheader entering canonical market evaluation.
- Any loss of sportsbook identity or opening/closing distinction.

## Handoff

Record mapping schema/statuses, coverage report path, unmatched/ambiguous
counts, commands run, tests run, and downstream MARKET-001 implications.
