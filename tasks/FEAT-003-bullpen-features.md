# FEAT-003 — Bullpen Features

## Status

blocked

## Dependencies

- DATA-004

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create point-in-time-safe bullpen quality and workload features.

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
