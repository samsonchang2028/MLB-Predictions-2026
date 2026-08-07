# FEAT-002 — Starting Pitcher Features

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

Create point-in-time-safe starting pitcher features for each game.

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
