# DATA-008 - Historical Odds Archive Ingestion

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

Ingest the finalized historical MLB odds archive immutably for V1 opening-line
market benchmarking.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `src/ingestion/odds/snapshots.py`
- `src/transforms/silver.py`

## Allowed files

- `src/ingestion/odds/`
- `tests/unit/ingestion/odds/`
- `tests/integration/ingestion/odds/`

## Source

- Release: `https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset`
- Asset: `mlb_odds_dataset.json`
- Published SHA-256:
  `3f952fd0bfae9f4f2d17e66692cb936ce6e1a5f6b415318012090c85933b882b`
- Approximate coverage: 2021-04-01 through 2025-08-16.

## Outputs

- Immutable Bronze copy of the downloaded archive.
- Bronze parsed records preserving sportsbook identity, teams, date/time fields
  available in the archive, opening moneylines, and closing/current-style
  moneylines where available.
- Source checksum/provenance sufficient for DATA-009 audit.

## Requirements

- Verify the downloaded source file SHA-256 before processing.
- Refuse to process a checksum mismatch.
- Preserve the downloaded source file immutably in Bronze.
- Do not make finding another historical odds provider a V1 blocker.
- Preserve opening moneyline odds as the canonical historical market benchmark.
- Preserve closing/current-style odds only as separate post-hoc benchmark fields
  where available.

## Critical correctness constraints

- Do not invent intraday observation timestamps for archive rows.
- Do not represent archive closing/current odds as timestamp-valid pregame odds.
- Keep this historical archive methodology separate from live timestamped odds
  snapshots.

## Acceptance criteria

- Re-ingestion is idempotent and does not duplicate canonical records.
- Checksum mismatch fails clearly.
- Moneylines parse as valid American odds.
- Sportsbook and team identity are retained.
- Opening moneyline fields are available for downstream mapping/audit.

## Required tests

- Unit tests for archive parsing and odds validation.
- Integration tests for immutable source retention and idempotent re-ingestion.
- Checksum mismatch regression test.
- Tests proving no fabricated observation timestamp is emitted.

## Merge-blocking conditions

- Source checksum is not verified.
- Raw archive file can be silently overwritten.
- Opening and closing/current-style prices are not distinguishable.
- Historical archive rows are merged into live timestamped snapshots without a
  clear methodology boundary.

## Handoff

Record raw artifact path, parsed table/schema, checksum result, coverage counts,
commands run, tests run, and known archive limitations.
