# FEAT-002 - Starting Pitcher Features

## Status

blocked

## Dependencies

- DATA-007

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create point-in-time-safe starting pitcher features for each game.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `tasks/DATA-007-historical-data-certification.md`

## Inputs

- Certified Silver pitcher appearances and starter identity from DATA-007.

## Allowed files

- `src/features/starter.py`
- `tests/unit/features/test_starter.py`
- `tests/leakage/test_starter_leakage.py`

## Initial features

- season ERA before game,
- WHIP,
- K rate,
- BB rate,
- K-BB rate,
- innings/start,
- days rest,
- previous-start workload,
- rolling recent-start metrics.

## Critical constraints

- current appearance excluded,
- appearances sorted chronologically,
- future starts cannot affect earlier rows,
- missing/changed starters handled explicitly.

## Acceptance criteria

- leakage mutation test passes,
- first-start behavior defined,
- starter changes do not silently use the wrong pitcher.

## Required tests

- unit tests for deterministic transforms and first-start behavior,
- leakage tests proving current/future pitcher appearances cannot affect
  earlier rows,
- regression tests for starter changes and missing starters.

## Merge-blocking conditions

- Missing or failed historical MLB data certification.
- Any current-game or future pitcher appearance leakage.
