# Research: Confirmed Batting Lineups as a Feature

Status: research only, no code changes. Written to inform a future task-graph
addition (`DATA-0XX` / `FEAT-00X` / `PIPE-00X`).

## 1. Data source and current repo state

### Fetch layer already exists; the parse layer doesn't

This repo already fetches the exact MLB Stats API payload that contains the
confirmed lineup — it just never reads that part of it.

- `src/ingestion/mlb/statsapi_fetchers.py::make_game_detail_fetcher` calls the
  wrapper's `game` endpoint (`statsapi.get("game", {"gamePk": ...})`), which is
  the `/api/v1.1/game/{gamePk}/feed/live` response. This is the same fetcher
  PIPE-005 already re-invokes right before prediction time via
  `refresh_pregame_game_details` in `scripts/daily_predictions.py`.
- That payload is stored byte-for-byte in Bronze (`bronze.mlb_game_detail_payloads`,
  per ADR-004) and is the only source `src/transforms/silver.py::normalize_silver`
  reads to populate `silver.pitcher_appearances` / `silver.pitcher_starters`.
- I inspected the repo's own fixture of this exact payload shape
  (`tests/fixtures/mlb/game_detail_717408_feed_live.json`) with a small script.
  Confirmed structure:

  ```
  liveData.boxscore.teams.home.battingOrder  -> [656941, 607208, 592206, 664761, 592663, 681082, 669742, 669016, 596117]
  liveData.boxscore.teams.home.players.ID656941.battingOrder -> "100"   # Schwarber, batting 1st, starter
  liveData.boxscore.teams.home.players.ID547180.battingOrder -> "901"   # Harper, batted 9th as a substitution
  ```

  `teams.{home,away}.battingOrder` is the ordered array of the 9 *starting*
  batter person IDs. Each player's own `battingOrder` string encodes slot
  (hundreds digit, 1-9) and substitution order (last digit; `0` = the original
  starter in that slot, non-zero = a later substitution) — this is the
  standard MLB Stats API convention, confirmed against the toddrob99
  `MLB-StatsAPI` wrapper docs/wiki (see Sources).
- Conclusion: **no new MLB endpoint and no new fetcher is needed.** The lineup
  is already inside every Bronze `mlb_game_detail_payloads` row this repo has
  ever stored or will refresh via PIPE-005's existing refresh path. This is
  purely a new Silver-parsing + Feature job, analogous to how `_pitcher_data`
  in `src/transforms/silver.py` already walks `liveData.boxscore.teams` for
  pitchers (`src/transforms/silver.py:248-326`) and `gameData.probablePitchers`
  for the pregame-known probable identity (`src/transforms/silver.py:329-338`).
- There is genuinely no batting-lineup table anywhere in Silver today —
  confirmed by grepping the repo for `lineup`/`battingOrder`/`batting_order`:
  the only hits are `docs/roadmap.md` (an aspirational mention, not an
  implementation) and this same test fixture.

### What's in the payload for "how good is this lineup"

Each player entry under `boxscore.teams.{side}.players.ID<id>` also carries
`seasonStats` / `stats` objects. The repo's fixture happens to be a
pitcher-focused fixture (its `seasonStats`/`stats` are trimmed to a `pitching`
sub-object with all zeros for a position player), so it does not itself prove
a `batting` sub-object with AVG/OBP/SLG/OPS is present for every game. That
matches known MLB Stats API `feed/live` behavior (each player's box entry
normally also has a `batting` stat block), but the concrete field set should
be re-verified against one *unmodified* real payload before FEAT work starts,
since fixtures here are hand-trimmed for test size. Do not assume it's richer
than it is — verify with one live pull first.

## 2. Same late-availability problem PIPE-005 already solved for pitchers

Confirmed lineups are announced later than probable pitchers — typically
2-4 hours before first pitch, sometimes with late scratches minutes before
game time. That is *strictly later and less certain* than probable-pitcher
timing, which MLB already publishes a day or more ahead. So a lineup feature
will be "unknown" for a larger fraction of any given prediction run than the
starter feature already is today, especially for early-morning/early-run
predictions.

This repo already has the exact mechanism for exactly this shape of problem —
it should be reused unchanged in spirit, not reinvented:

- **Refresh-before-predict**: `refresh_pregame_game_details` in
  `scripts/daily_predictions.py:165-198` invalidates and re-fetches today's
  slate's game-detail payloads right before load, then calls
  `normalize_silver`. A lineup Silver table populated by the same
  `normalize_silver` pass gets the same "freshest possible view at prediction
  time" property for free — no new refresh logic, just adding a lineup table
  to what that one pass already writes.
- **Explicit unknown placeholder, not implicit NaN**: `_starter_placeholder_rows`
  (`scripts/daily_predictions.py:390-417`) synthesizes an explicit
  `actual_pitcher_id=None, probable_pitcher_id=None` row for any
  `(game_pk, team_id)` missing from `silver.pitcher_starters`, so
  `build_starter_features` emits its already-defined "unknown starter" feature
  row (`starter_known=False`) instead of the row simply not existing. A lineup
  feature builder should follow the identical shape: a
  `_lineup_placeholder_rows` helper producing `(game_pk, team_id, lineup=None)`
  rows for any team missing from `silver.batting_lineups`, consumed by a new
  `build_lineup_features` that emits `lineup_known=False` / all-null metrics —
  never a missing row, never an invented lineup.
- **Two-level known/probable flag pattern**: `src/features/starter.py` exposes
  `starter_known` (identity resolved at all) and `starter_is_probable`
  (identity is the pregame probable, not the certified actual — line 34-37,
  259-265). A lineup feature has the same two axes and should reuse the same
  naming convention: `lineup_known` (any 9 IDs resolved for this team-game —
  in practice this will almost always mean *actual*, since MLB Stats API does
  not publish a separate "probable lineup" object the way it does
  `probablePitchers`; there is no pregame-probable equivalent to fall back to)
  and, if warranted, a `lineup_is_partial` flag for the case where fewer than
  9 batting-order slots have resolved yet (a lineup posted but not fully
  confirmed).
- **Unlike starters, a missing lineup should not gate/skip the game.** Today,
  `partition_schedule_by_announced_starters` (`scripts/daily_predictions.py:308-341`)
  skips a game entirely from prediction if either team's starting pitcher is
  unannounced (`SKIP_NO_STARTER_ANNOUNCED`), because starter identity is
  treated as load-bearing enough to justify not predicting. Lineup confirmation
  is later and less reliably available than starters — gating predictions on
  it would skip most early-run games. The natural design is: lineup is an
  **optional additive feature component** (like bullpen), never a skip
  condition. `starter_known`/`starter_is_probable` already establish this
  "known-flag + null-safe" pattern for a feature the Gold matrix must tolerate
  being absent; do the same for lineup rather than adding a second skip gate.

## 3. What a lineup feature would actually add over team-aggregate features

`src/features/team.py` already gives point-in-time team-level offense proxies
(`runs_scored_avg_before`, rolling `runs_scored_avg_L{7,14,30}`, etc. — see
`src/features/team.py:36-44` docstring: "OPS/OBP/SLG... are unavailable in the
current Silver contract; V1 offense proxies are runs scored/allowed only").
Those are backward-looking team run-scoring averages; they say nothing about
**who is in tonight's specific lineup**, which is the one thing a confirmed
lineup adds that a team aggregate structurally cannot:

- A team's season-long runs-scored average blends games with its best hitters
  in and games with regulars resting/injured/optioned. Tonight's *actual*
  lineup reveals, before first pitch, whether the team is fielding its normal
  best-available group or a weakened one — information the team aggregate
  averages over and dilutes.

Two candidate signal designs, ordered from simplest to more work, per
AGENTS.md's "smallest implementation that satisfies the need":

1. **Regular-starter presence/absence count (recommended first cut).** For
   each team, define its "typical lineup" as the N players who most often
   started at each of the 9 lineup slots (or just most often appeared as a
   position-player starter at all) over a trailing window (e.g. last 30 team
   games, mirroring the `FEATURE_WINDOWS` convention already used in
   `src/features/team.py:52`). Feature = count (or fraction) of tonight's
   confirmed 9 who are *not* in that typical set. This is a pure identity-set
   comparison — no batter-level rate stats needed at all, so it sidesteps the
   open question about `seasonStats.batting` shape entirely. It is a direct
   injury/rest/roster-shuffle proxy, cheap to compute and to reason about, and
   it is the piece of information a sportsbook bettor most obviously reacts to
   ("their $30M shortstop isn't playing tonight").
2. **Aggregate expected-lineup-strength score (second cut, only if #1 proves
   insufficient in a later evaluation).** Blend each confirmed batter's own
   recent point-in-time performance (e.g. a rolling wOBA/OPS-proxy computed
   the same shift-before-rolling way `starter.py`/`team.py` already do, but
   per-batter instead of per-team) into one team-level number, e.g. a simple
   unweighted or plate-appearance-weighted mean across the 9 confirmed
   batters. This requires: (a) confirming the payload's per-player batting
   stat block is actually populated and stable across seasons/games (open
   question from Section 1), and (b) a new per-batter rolling-history builder
   analogous to `starter.py`'s per-pitcher history, which is materially more
   surface area (a new dimension of entity — batter — the repo has never
   modeled before). This is explicitly the more-complex option and should not
   be built until #1 is shipped and shown not to capture what's needed —
   consistent with AGENTS.md: "Do not add abstractions for hypothetical future
   requirements."

Explicitly out of scope for both cuts, and not recommended at all absent a
concrete need: per-batter matchup modeling (vs. today's opposing starter's
handedness/repertoire), lineup order/protection effects, or any full
lineup-optimization/simulation system. AGENTS.md's engineering principles
("prefer the smallest implementation that fully satisfies the task," "do not
add abstractions for hypothetical future requirements") argue directly against
starting anywhere but option 1.

## 4. Point-in-time correctness requirements

Same rule as the rest of the repo (ADR-002, restated in AGENTS.md's "ML
correctness rules"): a lineup feature row for `(game_pk, team_id)` may only
use information knowable strictly before that game's prediction timestamp.

Concretely, mirroring `starter.py`'s existing contract almost field-for-field:

- The lineup used for a game's *own* feature row must be the lineup announced
  for **that game**, not a same-day double-header sibling or a later game.
  `silver.pitcher_starters` and `silver.pitcher_appearances` are already keyed
  by `(game_pk, team_id)` specifically to keep double-headers distinct
  (AGENTS.md: "Team/date alone is not a safe unique key because of
  doubleheaders and reschedules"); a new `silver.batting_lineups`-style table
  must use the same `(game_pk, team_id)` key, not `(team_id, date)`.
  Doubleheader lineups both live in the same `bronze.mlb_game_detail_payloads`
  fetch keyed by their own distinct `game_pk`, so this falls out for free from
  reusing the existing per-`game_pk` fetch/parse path — no special-casing
  needed.
- If a "typical lineup" or "batter rolling performance" history feature (per
  Section 3) is built, it must use only *that batter's* or *that team's*
  strictly-prior games, shift-before-rolling, exactly like
  `src/features/starter.py`'s per-pitcher history and `src/features/team.py`'s
  per-team history already do. A batter's current-game presence in the
  lineup is known pregame; a batter's current-game batting *line* (hits,
  etc.) is a post-game outcome and must never enter that same game's row,
  identical to the pitching-line rule already documented at the top of
  `src/features/starter.py`.
- The operator-side refresh must extend, not duplicate, PIPE-005's existing
  `refresh_pregame_game_details` — a lineup table populated by the same
  `normalize_silver()` call that already runs after invalidate+backfill needs
  no separate refresh step. The one thing that does need updating is
  `build_today_feature_components` (`scripts/daily_predictions.py:373-387`),
  which would gain a `lineup_rows = list(inputs["lineups"]) +
  _lineup_placeholder_rows(...)` line alongside the existing starter/bullpen
  wiring, and `load_prediction_inputs` would gain a `lineups` query mirroring
  the existing `starters` query (`scripts/daily_predictions.py:270-285`).
- ADR-002's ordering (`odds_snapshot < prediction_timestamp < first_pitch`)
  is unaffected — a lineup feature is just another pregame Gold column subject
  to the same completeness/inference-mode handling `build_feature_matrix`
  already does for starter/bullpen (`src/features/build.py`,
  `completeness_mode="inference"` usage in `scripts/daily_predictions.py:872`).

## 5. Proposed task breakdown

Following `tasks/index.md`'s table convention and ID prefixes (`DATA-` =
ingestion/Silver, `FEAT-` = feature engineering, `PIPE-` = pipeline/operator).
All new, Status `backlog`.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-018 | parse `liveData.boxscore.teams.{home,away}.battingOrder` (+ per-player `battingOrder` string) from the already-fetched `bronze.mlb_game_detail_payloads` into a new `silver.batting_lineups` table keyed `(game_pk, team_id, batting_order_slot)`; no new fetch, reuses existing game-detail payload and `normalize_silver` pass |
| DATA-023 | backlog | DATA-022 | verify actual (unmodified) MLB Stats API per-player `batting`/`seasonStats.batting` field shape and availability against a real payload pull; only needed if FEAT-008 (option 2, aggregate strength) is pursued — not required for FEAT-007 |
| FEAT-007 | backlog | DATA-022 | `build_lineup_features`: per `(game_pk, team_id)` typical-lineup-vs-tonight identity-set diff (regulars-out count/fraction), point-in-time-safe rolling "typical lineup" window analogous to `FEATURE_WINDOWS` in `src/features/team.py`; emits `lineup_known` (and `lineup_is_partial` if warranted) mirroring `starter_known`/`starter_is_probable` in `src/features/starter.py`; feeds into `src/features/build.py` as a new optional component, same absent-component handling as FEAT-005 |
| FEAT-008 | backlog | DATA-023, FEAT-007 | (optional, only if FEAT-007 proves insufficient) aggregate expected-lineup-strength score blending each confirmed batter's point-in-time rolling performance; new per-batter rolling-history builder analogous to `src/features/starter.py`'s per-pitcher history |
| PIPE-006 | backlog | FEAT-007, PIPE-005 | extend `scripts/daily_predictions.py`: add `lineups` query to `load_prediction_inputs`, `_lineup_placeholder_rows` helper mirroring `_starter_placeholder_rows`, wire `build_lineup_features` into `build_today_feature_components`; no new refresh step needed since PIPE-005's existing `refresh_pregame_game_details` -> `normalize_silver()` already repopulates `silver.batting_lineups` for the day's slate before load |

## Sources

- [toddrob99/MLB-StatsAPI GitHub repository](https://github.com/toddrob99/MLB-StatsAPI)
- [MLB-StatsAPI Endpoints wiki](https://github.com/toddrob99/MLB-StatsAPI/wiki/Endpoints)
- [MLB-StatsAPI Function: boxscore_data wiki](https://github.com/toddrob99/MLB-StatsAPI/wiki/Function:-boxscore_data)
- [MLB Starting Lineups Today — Confirmed Batting Orders, Probable Pitchers & Stats (NumberEdge)](https://app.numberedge.com/sports/mlb/lineups)
- Repo-internal: `tests/fixtures/mlb/game_detail_717408_feed_live.json` (inspected directly via a local script to confirm `liveData.boxscore.teams.{home,away}.battingOrder` and per-player `battingOrder` field shape)
