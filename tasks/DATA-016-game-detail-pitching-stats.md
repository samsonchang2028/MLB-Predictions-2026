# DATA-016 — Game-Detail Payload Omits All Pitching Stats (Re-ingest Required)

## Status

ready

## Dependencies

- DATA-011

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Severity

P0 for model quality. Every pitching feature is structurally empty on the real
certified build, so FEAT-002 (starter) and the ERA/WHIP half of FEAT-003
(bullpen) contribute nothing. The first real experiment trained on team features
only.

## Evidence

`silver.pitcher_appearances` has 132,848 rows and **100% NULL** for every stat
column, while identity columns are fully populated:

```
outs_recorded: 132848 null (100.0%)      hits_allowed: 132848 null (100.0%)
batters_faced: 132848 null (100.0%)      earned_runs:  132848 null (100.0%)
pitches_thrown: 132848 null (100.0%)     walks:        132848 null (100.0%)
innings_pitched: 132848 null (100.0%)    strikeouts:   132848 null (100.0%)
is_actual_starter: 0 null (0.0%)
```

The stored bronze payload is hollow (11,291 chars for a full live feed). Player
entries have populated `person` but empty stat objects:

```json
{"person": {"fullName": "Logan Webb", "id": 657277},
 "stats": {}, "seasonStats": {}, "position": {}, "gameStatus": {}}
```

Downstream effect in the real feature matrix (211 columns): 58 columns are
entirely empty — every `*_starter_roll_*`, `*_starter_season_*_before`,
`*_starter_prev_start_*`, `*_starter_days_rest`, and every
`*_bullpen_bullpen_era_*` / `*_bullpen_bullpen_whip_*`. No column is partially
filled (0% or 100% only), confirming a structural cause rather than sparse data.

## Root cause

`GAME_DETAIL_FIELDS` in `src/ingestion/mlb/game_detail.py` is an MLB `fields=`
allowlist, which the API applies at EVERY nesting level. `person`, `fullName`,
and `id` are listed (and are populated); `stats` and the pitching stat keys are
NOT listed, so the API returned `stats: {}` for every player. The existing
comment correctly avoided listing `players` (which would empty the subtree) but
omitted the stat keys underneath it.

## Goal

Persist the pitching stat lines so starter/bullpen features are real, then
re-ingest game details and confirm non-null coverage.

## Requirements

- Fix the field projection so boxscore player pitching stats survive: include the
  needed keys (e.g. `stats`, `pitching`, `inningsPitched`, `outs`,
  `battersFaced`, `numberOfPitches`, `hits`, `runs`, `earnedRuns`,
  `baseOnBalls`, `strikeOuts`, `homeRuns`) or drop the `fields` filter for this
  endpoint if a correct allowlist cannot be guaranteed.
- Prefer the smallest projection that provably returns the stats; do not silently
  retain play-by-play/pitch-level content if it can be avoided.
- Raw payloads remain immutable and ingestion idempotent; re-ingestion must be
  resumable (DATA-010 semantics) since the backfill is long (~4.5h previously).
- Add a test that FAILS on a hollow payload: assert that parsed appearances have
  non-null stat fields for a realistic fixture, so this cannot regress silently.
- After re-ingest, `silver.pitcher_appearances` stat columns must be
  substantially non-null and the feature matrix must have no fully-empty
  starter/bullpen columns.

## Notes

Re-ingestion is required because the data was never stored; it cannot be
recovered by re-running the silver transform. Coordinate the long run with the
Orchestrator (DuckDB is single-writer).

## Acceptance criteria

- Field projection fix with a regression test that fails on empty stat objects.
- Re-ingest completes and certification re-runs.
- Starter/bullpen feature columns are populated in the real matrix.
