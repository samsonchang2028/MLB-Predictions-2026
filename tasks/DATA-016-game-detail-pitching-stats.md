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

## The test that locked the bug in place

`tests/unit/ingestion/mlb/test_game_detail.py::test_players_subtree_is_not_field_filtered`
asserts that `stats`, `pitching`, `inningsPitched`, `earnedRuns`, `hits`, and
`numberOfPitches` are NOT in the allowlist. Its premise is only half true:
listing `players` itself does empty the subtree, but the stat LEAF keys must be
listed or the API returns `stats: {}`. Proof from the stored payload: `person`
IS in the allowlist and came back populated, while `stats` was omitted and came
back empty. This test must be corrected, not deleted, and its replacement must
encode the true invariant.

## Goal

Persist the pitching stat lines so starter/bullpen features are real, then
re-ingest game details and confirm non-null coverage. Make the request/response
contract provable so it cannot silently regress.

## Requirements

- Fix the field projection so boxscore player pitching stats survive: include the
  needed keys (e.g. `stats`, `pitching`, `inningsPitched`, `outs`,
  `battersFaced`, `numberOfPitches`, `hits`, `runs`, `earnedRuns`,
  `baseOnBalls`, `strikeOuts`, `homeRuns`) or drop the `fields` filter for this
  endpoint if a correct allowlist cannot be guaranteed.
- CORRECT `test_players_subtree_is_not_field_filtered` to assert the true
  invariant (`players` itself stays unlisted; the stat leaf keys ARE listed).
  Document why, citing the populated-`person` vs empty-`stats` evidence.
- HOLLOW-PAYLOAD GUARD: parsing a completed game whose boxscore yields pitcher
  appearances with NO stat values must be an explicit ingestion failure/status,
  not a silently persisted row. A Final game with `stats: {}` for every pitcher
  is a defect, and ingest is the earliest place to see it.
- REAL-PAYLOAD CONTRACT FIXTURE: commit one recorded REAL `feed/live` response
  (captured with the production `fields` string, trimmed of irrelevant subtrees
  if large) as a golden fixture, and assert the parser extracts non-null stat
  lines from it. This is the check that would have caught the bug offline.
- LIVE SMOKE CHECK: add an opt-in script/marker (network, excluded from the
  default suite) that fetches ONE game with the production `fields` string and
  asserts the response contains non-empty pitching stats. Document how to run it
  before any long backfill.
- Raw payloads remain immutable and ingestion idempotent; re-ingestion must be
  resumable (DATA-010 semantics) since the backfill is long (~4.5h previously).
- PRESERVE THE MINIMAL-PROJECTION PHILOSOPHY: request the pitching stats that
  FEAT-002/FEAT-003 actually require; do not request play-by-play, pitch-level, or
  other unnecessary MLB data. Justify each added key.
- VERIFY AGAINST REAL MLB RESPONSES, not fixtures alone.
- SMOKE TEST BEFORE ANY FULL BACKFILL: a small real-data smoke backfill covering
  MULTIPLE COMPLETED games (across more than one season/date) must run first, and
  its pitcher-stat population must be inspected before the full re-ingest is
  launched. The Orchestrator gates the long run on this result.
- DO NOT accept a payload merely because a `stats` object EXISTS. Assert the
  required NESTED values are actually present and meaningful for the fields
  FEAT-002/FEAT-003 consume: innings pitched, outs, earned runs, hits allowed,
  walks, strikeouts, batters faced (and pitches where required). An empty or
  all-zero-across-the-board stat object is a failure, not a pass.
- Confirm ACTUAL STARTERS AND RELIEVERS remain identifiable after the projection
  change (`is_actual_starter` / appearance ordering must still resolve).
- The repaired backfill must regenerate the affected Bronze/Silver lineage using
  the existing immutability/versioning conventions. Do NOT silently reuse the
  hollow historical pitcher data.
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
