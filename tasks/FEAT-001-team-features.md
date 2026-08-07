# FEAT-001 — Team Features

## Status

implementing

## Dependencies

- DATA-004

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create point-in-time-safe team strength and recent-form features.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-003-validation.md`
- `src/transforms/silver.py` (Silver keys and post-game field contract)

## Inputs

- `silver.games`
- `silver.team_game_statistics` (treat `score`, `is_winner`, and Final-row `league_*` as post-game only; use prior games after shift)

## Allowed files

- `src/features/team.py`
- `tests/unit/features/test_team.py`
- `tests/leakage/test_team_leakage.py`

## Required outputs

- One deterministic feature row per `(game_pk, team_id)` (or equivalent documented team-game key) available at a pregame prediction timestamp.

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

## Required tests

- unit tests for deterministic transforms and window edges,
- leakage mutation tests proving future/current-game results cannot change earlier feature rows,
- first-game / cold-start behavior defined.

## Handoff

Document feature keys, prediction-time semantics, commands run, gate results, and any metrics omitted due to missing Silver fields.
