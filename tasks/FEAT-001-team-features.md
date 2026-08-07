# FEAT-001 — Team Features

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

Create point-in-time-safe team strength and recent-form features.

## Allowed files

- `src/features/team.py`
- `tests/unit/features/test_team.py`
- `tests/leakage/test_team_leakage.py`

## Requirements

Initial V1 candidates:

- win percentage before game,
- run differential,
- runs scored/allowed,
- recent 7/14/30-game windows,
- offense metrics available from normalized data.

## Critical constraints

- shift before rolling,
- current game excluded,
- future rows cannot affect earlier features,
- deterministic home/away orientation.

## Acceptance criteria

- one deterministic feature row per team-game key,
- leakage mutation test passes,
- window edge cases tested.
