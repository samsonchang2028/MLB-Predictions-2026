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

### Summary

Added `src/ingestion/kalshi/matching.py`: `match_kalshi_market()` maps one
Kalshi market object (the same shape DATA-022 already parses -- `ticker`,
`event_ticker`, `yes_sub_title`, `no_sub_title`, `occurrence_datetime`) to a
`game_pk` against a list of `KalshiGameCandidate` schedule entries, returning
a `KalshiMatchResult` with an explicit `MATCHED` / `UNMATCHED` / `AMBIGUOUS`
status and machine-readable `reason` (`no_team_match`,
`time_out_of_tolerance`, `ambiguous_nearest_time`, `matched`) -- never a bare
`None`. `summarize_match_results()` rolls a list of results into
`matched_events` / `mapped_games` / `unmatched_events.<reason>` counts, the
same shape `odds_stats` already uses in `scripts/daily_predictions.py`.
`kalshi_game_candidates_from_schedule()` builds `KalshiGameCandidate` objects
from `silver.games`-shaped rows (`game_pk`, `game_date`, `source_game_json`),
mirroring `_mlb_team_names`/`_candidate_team_names` elsewhere in the repo, so
a future PIPE-006 wiring pass has a ready-made loader.

Matching logic, mirroring `_match_schedule_game`'s pattern without reusing
the function itself:
- Team identity: Kalshi's `yes_sub_title`/`no_sub_title` are short city-style
  names ("Seattle"), not this repo's full club names ("Seattle Mariners").
  Normalization (`normalize_kalshi_team_name`) matches `_normalize_team_name`
  style (casefold, strip periods, collapse whitespace); matching itself is
  word-subset containment (Kalshi's normalized words ⊆ schedule team's
  normalized words), not equality, since equality would never match a city
  name against a full club name. Kalshi doesn't declare which side is
  home/away, so both team names are checked against the schedule pair in
  either order.
- Doubleheader disambiguation: candidates are ranked by absolute distance
  from `occurrence_datetime`, same tie-break-is-ambiguous /
  nearest-wins-if-unique logic as `_match_schedule_game`. The 12-hour
  tolerance (`MATCH_TIME_TOLERANCE_SECONDS`) is the same value as
  `scripts/daily_predictions.py`'s `ODDS_MATCH_TOLERANCE_SECONDS`, kept as a
  local constant rather than imported (importing the operator script would
  make an `src/` module depend on `scripts/daily_predictions.py`, which pulls
  in duckdb/xgboost/network imports at module load time -- a backwards
  dependency direction this repo doesn't use elsewhere).

### Files changed

- `src/ingestion/kalshi/matching.py` (new)
- `tests/unit/ingestion/kalshi/test_matching.py` (new)

### Commands run

- `python -m pytest tests/unit/ingestion/kalshi/ -q` -> **40 passed**
- `python -m pytest -q` (full suite) -> **821 passed, 5 xfailed**

### Known limitations / judgment calls

- **City-name-only team matching is unverified for multi-club cities.** The
  only real captured Kalshi payload (`tests/unit/ingestion/kalshi/fixtures/
  kalshi_market_snapshots.json`) is Seattle @ Houston -- no city shared by two
  MLB clubs. The word-subset containment match would accept a bare
  `"Chicago"` against *either* the Cubs' or White Sox' full name, `"New
  York"` against Yankees or Mets, `"Los Angeles"` against Dodgers or Angels.
  Since the opposing team's name is also required to match the *other* side
  of the same candidate game, this is not expected to misroute in practice
  (the two names together pin one specific matchup, and the near-start-time
  tolerance still filters far-off games), but it has not been checked against
  a real Kalshi payload for one of these shared-city matchups. If a live
  payload for one of these teams turns out to use something other than a
  bare city name (e.g. `"Cubs"`, `"NY Yankees"`), the containment match still
  works (word-subset still holds); the real risk is only if Kalshi ever
  produces an identical bare-city string for both clubs *and* the matching
  logic downstream needs to know which specific club, which this module
  fully requires (opponent name + start time) already provides.
- **Home/away is not inferred from Kalshi's title/ticker.** The title format
  (`"Seattle vs Houston Winner?"`) does not reliably declare which side is
  home; this module deliberately does not guess and checks the two team
  names against the schedule pair unordered. This is a strictly safer
  simplification (no orientation claim is made), not a scope gap.
- **`occurrence_datetime` is confirmed** as the correct implied-game-datetime
  field to use (not `open_time`/`close_time`, which are the market's trading
  window) via the real DATA-022 fixture and the orchestrator-supplied
  context; not directly re-verified against a fresh live API fetch in this
  task.
- **`bronze.kalshi_market_snapshots` (DATA-022) does not currently persist
  `occurrence_datetime`, `title`, `yes_sub_title`, or `no_sub_title`** --
  only `side` (which stores `yes_sub_title`'s value) survives into Bronze.
  `match_kalshi_market()` is written against the raw Kalshi market-object
  shape (same shape as the fixture/API response), not the Bronze row shape,
  because the Bronze row alone is insufficient to reconstruct a match
  (`no_sub_title` and `occurrence_datetime` are absent). Wiring this into a
  live pipeline (PIPE-006) will need either a Bronze schema addition to
  persist those fields or a call site that matches at fetch time before/
  alongside Bronze storage rather than after. Flagging this now since it
  will otherwise resurface as a confusing gap when PIPE-006 starts.
- No ADR change needed; no `state/CURRENT.md` update made by this task (the
  orchestrator's existing "Recently shipped (Kalshi integration, wave 1)"
  section names DATA-023 as unblocked next -- updating that ledger is left to
  the orchestrator/reviewer per this repo's normal handoff flow).

### Re-review fix (P1 + P2)

An independent Reviewer returned CHANGES REQUIRED with one P1 and one P2 in
`src/ingestion/kalshi/matching.py`; an independent Tester (PASS) had also
found and pinned a lower-severity version of the same P1 issue plus an
unrelated cosmetic P3.

**P1 fixed.** `match_kalshi_market` could silently resolve to the WRONG
`game_pk` when word-subset city-name matching produced candidates spanning
more than one genuinely distinct team pair (e.g. a Kalshi "Chicago"/"New
York" market meant for Cubs@Mets, but an unrelated same-day White Sox@Yankees
game happened to be closer in time and won on nearest-time ranking). Fix:
before nearest-time tie-breaking, group surviving candidates by
`frozenset({home_team_norm, away_team_norm})`. If more than one distinct team
pair survives, this is team-identity ambiguity, not doubleheader-timing
ambiguity -- return `AMBIGUOUS` with a new reason string,
`"ambiguous_team_identity"`, unconditionally, without consulting time
distance at all. If exactly one distinct team pair survives (the normal
doubleheader case), behavior is unchanged: falls through to the existing
nearest-time logic. Evidence: new test
`test_reviewer_p1_regression_unrelated_same_day_collision_does_not_win_on_time`
reproduces the Reviewer's exact scenario (true target Cubs@Mets further from
`occurrence_datetime` than an unrelated closer White Sox@Yankees candidate)
and asserts `AMBIGUOUS` / `ambiguous_team_identity`, not a wrong `MATCHED`.

Two existing tests were built on the pre-fix behavior and required updating
(not weakening -- both still assert a correct, now-different outcome):
- `test_reciprocal_multi_city_ambiguity_with_tied_times_is_explicit_ambiguous`:
  still `AMBIGUOUS`, but reason changed from `"ambiguous_nearest_time"` to
  `"ambiguous_team_identity"` (team-identity ambiguity is now detected before
  the time tie-break is ever reached).
- Renamed
  `test_reciprocal_multi_city_ambiguity_near_tie_resolves_by_time_not_team_identity`
  to `test_reciprocal_multi_city_ambiguity_near_tie_is_ambiguous_not_resolved_by_time`
  and changed its assertion from `MATCHED`/`game_pk == 301` to `AMBIGUOUS`/
  `reason == "ambiguous_team_identity"` -- this was the exact near-tie
  scenario the Tester had pinned as a documented P2 risk; the fix
  deliberately changes that documented behavior on purpose, so the test now
  documents the corrected behavior instead of the bug.

**P2 fixed.** `summarize_match_results`'s `mapped_games` counted one
increment per matched *market*, but Kalshi issues two markets per real game
(yes/no sides), so it double-counted real games. Fix:
`stats["mapped_games"] = len({result.game_pk for result in results if
result.status == MATCHED})` (dedupe by `game_pk`). Evidence: new test
`test_summarize_match_results_dedupes_mapped_games_across_yes_no_market_sides`
matches both real fixture markets (yes/no sides of the same Seattle@Houston
event) and asserts `matched_events == 2` but `mapped_games == 1`.

**Not touched (by design):** the Tester's P3 (`normalize_kalshi_team_name`
period-without-space collapsing, e.g. `"St.Louis"`) -- cosmetic, currently
unreachable with real data, left exactly as documented.

**Test evidence:**
- `python -m pytest tests/unit/ingestion/kalshi/ -q` -> **60 passed** (was 58
  before this fix round; +2 net new tests: the P1 regression test and the
  P2 `mapped_games` dedupe test; the two updated tests are edits, not new
  additions).
- `python -m pytest -q` (full repo suite) -> **841 passed, 5 xfailed** (was
  821 passed, 5 xfailed before this fix round; delta matches the +20 kalshi
  test file changes/additions net of edits, no regressions elsewhere).

Commit: `c6e8e5c` on `agent/DATA-023-kalshi-game-matching`.
