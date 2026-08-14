# DATA-023 — Kalshi Event → game_pk Matching

## Status

backlog

## Dependencies

- DATA-022
- DATA-002 (MLB schedule ingestion — `silver.games` is the match target)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

## Goal

Map Kalshi's per-game markets to this repo's canonical `game_pk`, the same
kind of problem `scripts/daily_predictions.py`'s `_schedule_matchup_index`/
`_match_schedule_game` already solves for The Odds API's team-name-based
events — but Kalshi identifies games by market ticker/title text, not a
structured home/away team-name pair, so this needs its own matching logic,
not a reuse of the existing function as-is.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/researcha/kalshi-integration.md`
- `scripts/daily_predictions.py` (`_schedule_matchup_index`, `_match_schedule_game`,
  `_normalize_team_name` — read the exact matching/tolerance logic this
  should mirror in spirit, even though the parsing of Kalshi's identifiers
  will differ)
- `src/validation/odds_mapping.py` (DATA-009's MATCHED/UNMATCHED/AMBIGUOUS
  audit pattern for the historical odds archive — this task's live-matching
  problem is smaller in scope than DATA-009's but should produce the same
  kind of explicit, visible outcome categories rather than silently dropping
  unmatched events)

## Allowed files

- `src/ingestion/kalshi/matching.py` (new)
- `tests/unit/ingestion/kalshi/test_matching.py` (new)

## May modify if necessary

- none

## Do not modify

- `scripts/daily_predictions.py`'s existing `_match_schedule_game` (sportsbook
  matching stays untouched; this is a parallel function, not a shared
  generalization — do not force both matching problems into one abstraction
  for two call sites)

## Inputs

- Kalshi market tickers/titles from DATA-022's ingested rows (exact title
  format needs confirming against a real payload during implementation — the
  research doc did not verify the precise title string format)
- `silver.games` (canonical schedule: `game_pk`, team ids, `game_date`)

## Outputs

- `src/ingestion/kalshi/matching.py`: a function mapping a Kalshi market
  identifier + implied game datetime to a `game_pk`, returning an explicit
  match/no-match/ambiguous result (not a bare `None` on failure) so unmatched
  Kalshi markets are visible, not silently dropped — matches this repo's own
  stated convention of never silently discarding unmatched events.

## Requirements

- Team identity extraction from Kalshi's ticker/title text (whatever format
  DATA-022 confirms) must normalize the same way `_normalize_team_name` does
  for sportsbook team names (case-fold, strip punctuation) so both matchers
  are at least stylistically consistent, even if the actual parsing logic
  differs because the input format differs.
- Time-based disambiguation for doubleheaders (same two teams, same date, two
  games) — reuse the near-start-time-tolerance pattern from
  `_match_schedule_game` rather than inventing a new tolerance value out of
  nowhere.

## Critical correctness constraints

- No silent drops: every Kalshi market that doesn't resolve to exactly one
  `game_pk` must surface as an explicit unmatched/ambiguous case, following
  this repo's existing convention (`_match_schedule_game`'s
  `no_team_match`/`time_out_of_tolerance`/`ambiguous_nearest_time` reasons)
  rather than a bare `None`/empty result a caller could accidentally ignore.

## Acceptance criteria

- A fixture set of Kalshi markets (including at least one doubleheader-style
  ambiguous case and one genuinely unmatched case) resolves correctly against
  a fixture schedule.
- Match reasons are visible/loggable the same way `odds_stats` in
  `scripts/daily_predictions.py` surfaces `unmatched_events.*` counts today.

## Required tests

- unit: matched/unmatched/ambiguous cases against fixture data, mirroring
  `tests/unit/scripts/test_daily_predictions.py`'s existing
  `test_odds_snapshots_leave_unmatched_events_visible`-style coverage

## Handoff

Record: summary, files changed, commands run, test results, known
limitations (especially: confirmed Kalshi title/ticker format from a real
payload, or still working from the research doc's unverified description?),
any new ADR/state changes.
