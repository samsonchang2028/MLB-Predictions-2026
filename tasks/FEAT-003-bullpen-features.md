# FEAT-003 - Bullpen Features

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

Create point-in-time-safe bullpen quality and workload features.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `tasks/DATA-007-historical-data-certification.md`

## Inputs

- Certified Silver pitcher appearances from DATA-007.

## Allowed files

- `src/features/bullpen.py`
- `tests/unit/features/test_bullpen.py`
- `tests/leakage/test_bullpen_leakage.py`

## Initial features

- bullpen ERA/WHIP over recent windows where available,
- innings over prior 1/3 days,
- pitches or workload proxy over prior 1/3 days,
- recent bullpen usage.

## Critical constraints

- exclude current game,
- use prior timestamps only,
- preserve team identity,
- doubleheaders handled chronologically.

## Acceptance criteria

- same-day doubleheader ordering is tested,
- future bullpen appearances cannot affect earlier games,
- workload windows are deterministic.

## Required tests

- unit tests for deterministic workload windows,
- leakage tests proving current/future bullpen appearances cannot affect
  earlier rows,
- regression tests for same-day doubleheaders.

## Merge-blocking conditions

- Missing or failed historical MLB data certification.
- Any current-game or future bullpen appearance leakage.
