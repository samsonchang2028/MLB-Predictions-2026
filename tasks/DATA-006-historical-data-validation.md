# DATA-006 - Historical MLB Data Validation

## Status

done (merged 1d9b83b)

## Dependencies

- DATA-005

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Create formal validation checks for the 2021-2025 MLB historical dataset before
downstream feature/model work relies on it.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `src/transforms/silver.py`
- `src/features/team.py`

## Allowed files

- `src/validation/`
- `tests/unit/validation/`
- `tests/integration/validation/`
- `tests/leakage/`

## Inputs

- Bronze MLB schedule and game-detail data.
- Silver normalized games, team statistics, and pitcher appearances.
- Existing feature builders where temporal regression checks require them.

## Outputs

- Reusable validation checks with structured PASS/FAIL/WARN results.
- A minimal validation runner suitable for certification.

## Requirements

- Validate Bronze integrity: expected raw artifacts, hashes, malformed payloads,
  accidental overwrites, provenance, and deterministic processing.
- Validate game identity: `game_pk` uniqueness, duplicate detection, home team
  differs from away team, valid team IDs, valid game statuses.
- Validate referential integrity: game-to-team, game-to-pitcher,
  pitcher-appearance-to-game relationships.
- Validate results: completed games have valid scores/results, `home_win`
  derivation is correct, impossible values are rejected.
- Validate pitching: actual starter identification, pitcher appearance
  consistency, bullpen appearance consistency, innings/stat domains.
- Explicitly test doubleheaders, postponements, reschedules, suspended games
  where applicable, cancelled games, starter changes, and same-day games between
  the same teams.

## Temporal/leakage requirements

- Current-game information cannot enter current-game features.
- Future games cannot alter historical features.
- Rolling features shift before rolling.
- Game N only sees eligible information from before game N.
- Training/test folds are chronological.
- Train/test overlap is impossible.
- Preprocessing fits only on the training partition.
- Calibration does not see evaluation targets improperly.
- 2026 cannot enter model/window/calibration selection.
- Historical game features use MLB information available strictly before the
  target game's cutoff.

## Required future-mutation regression

1. Build historical features.
2. Mutate or add future source data.
3. Rebuild earlier features.
4. Assert earlier features remain unchanged.

## Acceptance criteria

- Validation results are deterministic.
- Each check reports enough context to identify failing records.
- P0/P1 data and leakage failures are distinguishable from warnings.
- The runner exits or reports failure in a way DATA-007 certification can
  consume.

## Required tests

- Unit tests for deterministic validation functions.
- Integration tests against small Bronze/Silver fixtures.
- Dedicated leakage tests for feature time safety and chronological folds.
- Regression tests for future-mutation invariance.

## Merge-blocking conditions

- Any leakage test failure.
- Any validation path that hides duplicate, ambiguous, or referentially invalid
  data as a generic dedupe.
- Any 2026 data entering development selection checks.

## Handoff

Record validation check inventory, severity policy, commands run, test results,
known validation gaps, and how DATA-007 should consume the results.
