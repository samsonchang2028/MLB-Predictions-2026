# DATA-004 — Normalized Game and Odds Datasets

## Status

done

## Dependencies

- DATA-002
- DATA-003

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Create stable Silver-layer normalized datasets and the MLB↔odds mapping contract.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`

## Inputs

- Bronze MLB schedule tables produced by DATA-002.
- Bronze odds snapshot tables produced by DATA-003.
- Source payload, observation, and commence timestamps retained by ingestion.

## Allowed files

- `src/transforms/`
- `tests/unit/transforms/`
- `tests/integration/transforms/`

Orchestrator-authorized repair expansion (mapping safety):

- `src/ingestion/odds/`
- `tests/unit/ingestion/odds/`
- `tests/integration/ingestion/odds/`

## Required outputs

At minimum:

- games,
- team-game statistics,
- pitcher-game/appearance statistics,
- odds snapshots,
- mapping between source odds event and MLB `game_pk`.

## Critical constraints

- joins have explicit cardinality assertions,
- one game is not identified by team/date alone,
- no silent many-to-many joins,
- source timestamps preserved.

## Acceptance criteria

- deterministic normalized tables,
- unique keys documented/tested,
- doubleheaders map correctly,
- unmapped odds events are surfaced rather than silently attached.

## Required tests

- deterministic repeated normalization,
- unique-key and join-cardinality assertions,
- doubleheader mapping,
- unmapped and ambiguous odds-event handling,
- source-timestamp preservation.

## Handoff

Document normalized table keys, mapping cardinalities, commands run, gate results, and any unmapped/ambiguous-event limitations.

## Completion handoff

- Built deterministic Silver rebuild from Bronze: `games`, `team_game_statistics`, empty `pitcher_appearances` contract, `odds_snapshots`, `odds_event_game_mapping`.
- Keys: `game_pk`; `(game_pk, team_id)`; `(game_pk, team_id, pitcher_id, appearance_order)`; odds snapshot PK; one mapping row per `(source, source_event_id)`.
- Odds→game mapping requires exact commence time **and** case-insensitive provider vs MLB `game_json` team-name match. Incomplete concurrent slates no longer wrong-attach. Unmapped/ambiguous stay without `game_pk`.
- Bronze odds now retain `home_team`/`away_team` (already parsed; previously dropped). Legacy NULL team columns remain unmapped until odds are rebuilt from raw.
- `score` / `is_winner` / Final `league_*` labeled as post-game (ADR-002); not pregame features.
- `python -m pytest`: 84 passed. Reviewer APPROVE; Tester PASS after one repair loop. Deferred P2: legacy NULL team backfill; no alias table for name drift.
- No ADR change. FEAT-001 can unlock after metadata check; FEAT-002/003 remain limited by empty `pitcher_appearances` until appearance-capable ingestion exists.
