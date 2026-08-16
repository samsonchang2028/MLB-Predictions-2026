# PIPE-006 — Kalshi Pregame Capture (Near First Pitch, Not Daily-Batch)

## Status

implemented, awaiting review

## Dependencies

- DATA-022
- DATA-023
- MARKET-003
- PIPE-004 (the `odds_books.jsonl` artifact/schema this writes into)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

## Goal

Capture each game's Kalshi price **once, close to that specific game's first
pitch** — explicitly NOT on the same single once-daily batch timestamp
`scripts/daily_predictions.py` currently uses to fetch every sportsbook price
for the whole slate at once. This is a deliberate, user-specified design
choice (Kalshi's price is more meaningful as a closing-style, near-game-time
read than as a morning snapshot alongside everything else), not a limitation
to work around — do not "fix" this by just folding Kalshi into the existing
single daily fetch.

A 15-game slate has games starting at meaningfully different times throughout
the day (afternoon getaway games, night games, doubleheaders). A single
once-a-day cron cannot satisfy "near first pitch" for every game on the slate
simultaneously — this is fundamentally a *scheduling* requirement, not just a
code change. Read the cross-cutting scheduling research before scoping this
task's actual implementation.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/researcha/kalshi-integration.md`
- `docs/researcha/ops-001-automation-and-scheduling.md` — specifically the
  section on arbitrage/live-monitoring needing a different, higher-frequency
  automation shape than the rest of this repo's once-daily cadence; **this
  task's "near first pitch, per game" requirement is the same class of
  problem**, not a new one. Reuse that research's proposed `OPS-003`
  direction rather than re-deriving a scheduling design from scratch here.
- `tasks/OPS-001-daily-operator-automation.md` if it exists as a real task
  file (check `tasks/index.md` for current status — do not assume it's still
  a one-line backlog entry, confirm)
- `scripts/daily_predictions.py` (`fetch_odds_payload`, the two-call pattern
  PIPE-004 already established for canonical-vs-comparison odds fetches —
  this task adds a THIRD, differently-timed fetch path for Kalshi, following
  the same "isolate comparison-only data behind a try/except, never let it
  break the canonical prediction run" principle PIPE-004's post-review fixes
  established)

## Orchestrator-resolved decisions (confirmed with user, do not re-litigate)

- **Scheduling mechanism: new standalone script + external scheduler.** Build
  `scripts/kalshi_pregame_capture.py` as its own entry point, intended to run
  frequently (e.g. every 10-15 minutes via cron/Task Scheduler) and capture
  any game whose first pitch falls within the tolerance window and hasn't
  been captured yet. Do not fold this into `scripts/daily_predictions.py`'s
  own run loop. The actual scheduler wiring (cron entry, Task Scheduler job,
  etc.) is out of scope for this task — implement and document the script's
  own idempotent "capture whatever's due right now" behavior; a later
  OPS-001/OPS-003 task owns the actual scheduler registration.
- **Bronze schema gap: add columns to `bronze.kalshi_market_snapshots`.**
  DATA-022's table does not currently persist `occurrence_datetime`,
  `title`, or `no_sub_title` — fields DATA-023's `matching.py` needs and
  currently expects on the raw Kalshi market-object shape, not the Bronze
  row shape. Extend the Bronze table/ingestion (`src/ingestion/kalshi/
  snapshots.py`) to persist these additively (new nullable columns, existing
  rows unaffected), then have this task's capture script read matching
  inputs from Bronze like every other ingestion-consumer in this repo,
  rather than matching directly against the raw API response before
  persistence. This keeps ingestion and matching decoupled, consistent with
  how the rest of the Bronze layer works.

## Allowed files

- `scripts/kalshi_pregame_capture.py` (new)
- `tests/unit/scripts/test_kalshi_pregame_capture.py` (new)
- `src/ingestion/kalshi/snapshots.py` (additive schema change only — new
  nullable columns + parsing for `occurrence_datetime`/`title`/
  `no_sub_title`; do not change existing column semantics or break DATA-022's
  existing tests)
- `tests/unit/ingestion/kalshi/test_kalshi_snapshots.py` (extend for the new
  columns)
- `tests/integration/ingestion/kalshi/test_kalshi_ingestion.py` (extend if
  needed for the new columns)

## May modify if necessary

- `state/predictions/odds_books.jsonl`'s writer call site (additive: Kalshi
  rows use the same `(run_date, game_pk, bookmaker)` upsert key and
  `on_conflict="overwrite"` semantics PIPE-004 already established — `source`
  field distinguishes `"kalshi"` from `"the_odds_api"` bookmaker rows)

## Do not modify

- `odds_snapshots_for_schedule` / the canonical DraftKings-pinned prediction
  path (unaffected by anything here, same boundary PIPE-004 established)
- `append_jsonl_records`'s core contract in `scripts/daily_predictions.py`
  (reuse it for the Kalshi write, don't fork a fourth writer)

## Inputs

- DATA-022's Kalshi ingestion + DATA-023's game_pk matching
- Each game's `game_start_timestamp` (already available from `silver.games` /
  the daily schedule load) — the actual trigger for "how close to first
  pitch is close enough" (propose and document a concrete tolerance, e.g.
  30-60 minutes before first pitch, as a starting default; this is a product
  decision to confirm with the user before locking in, not something to pick
  silently)

## Outputs

- `state/predictions/odds_books.jsonl` rows with `bookmaker: "kalshi"`
  (converted to American-odds-equivalent via MARKET-003's helper before
  writing, so `src/app/game_detail.py`'s existing loading/display code needs
  ZERO changes to pick these up), `snapshot_timestamp` reflecting when the
  near-first-pitch Kalshi read actually happened (which will differ per game,
  unlike sportsbook rows which currently share one daily batch timestamp).

## Requirements

- Never blocks or fails the canonical prediction pipeline — a missing,
  unmatched, or errored Kalshi fetch for one game must not prevent
  predictions for that game (or any other) from being produced, following
  PIPE-004's established try/except-isolation pattern for comparison-only
  data.
- Must not fetch Kalshi data for a game more than once close to first pitch
  in the intended design (avoid needless repeated polling of the same
  near-game-time window) — but IS expected to run once per game, at a
  different wall-clock time per game, which is the entire point of this task
  and the reason it can't just be a code change inside the existing
  once-a-day script without also changing how/when that script (or a new
  one) gets invoked.

## Critical correctness constraints

- `daily.jsonl` / `market_probability` / `edge` remain untouched — Kalshi is
  comparison-only, same non-negotiable boundary as every other book PIPE-004
  established.
- Point-in-time correctness: the Kalshi snapshot used must be timestamped
  strictly before that specific game's first pitch (reuse
  `snapshot_is_pregame_valid`-style reasoning from `src/market/engine.py`,
  even though this data never enters the canonical `evaluate_pregame` path —
  a comparison price captured mid-game or post-game would be misleading to
  display as if it were a pregame read).

## Acceptance criteria

- For a slate with games at different start times, each game's Kalshi row
  has a `snapshot_timestamp` close to (and before) that specific game's first
  pitch, not a single shared timestamp for the whole slate.
- A missing/errored Kalshi fetch for one game does not affect any other
  game's prediction or Kalshi data.

## Required tests

- unit: near-first-pitch timing logic (games at different start times each
  get their own appropriately-timed capture, not one shared batch time)
- unit: failure isolation (a Kalshi fetch error for one game doesn't cascade)
- integration: end-to-end with a multi-game, multi-start-time fixture slate

## Handoff

Record: summary, files changed, commands run, test results, known
limitations, and — critically — the actual scheduling mechanism decided on
(a new script + external scheduler entry, a modification to the existing
daily script's own internal loop with per-game sleep/wait logic, or
something else) and how it relates to whatever `OPS-001`/`OPS-003` ends up
being, since this task cannot be fully "done" in isolation from that
scheduling decision — flag this explicitly rather than silently declaring it
solved.

### Implementer handoff (2026-08-15)

**Summary.** Added `scripts/kalshi_pregame_capture.py`, a new standalone
entry point that captures each due game's Kalshi price once, near that
specific game's own first pitch, and upserts it into the existing
`state/predictions/odds_books.jsonl` as a `bookmaker="kalshi"` row
(`source="kalshi"`), converted to American-odds-equivalent via MARKET-003's
`probability_to_american`. Extended `bronze.kalshi_market_snapshots`
(DATA-022) additively with three new nullable columns
(`occurrence_datetime`, `title`, `no_sub_title`) so this script can read
DATA-023's matching inputs from Bronze, like every other Bronze consumer in
this repo, instead of matching against the raw Kalshi payload directly.
`daily.jsonl`, `market_probability`, and `edge` are untouched — this script
never imports or calls anything in the canonical prediction path.

**Scheduling mechanism (per the orchestrator-resolved decision — read this
before assuming the task is "done").** This is a new standalone script,
`scripts/kalshi_pregame_capture.py`, meant to be invoked frequently (e.g.
every 10-15 minutes) by an external scheduler (cron / Windows Task
Scheduler). **No scheduler registration was built or configured by this
task** — that is explicitly deferred to a future OPS-001/OPS-003 task, per
`docs/researcha/ops-001-automation-and-scheduling.md`'s recommendation to
give high-frequency/windowed polling (arbitrage, live monitoring, and now
this near-first-pitch Kalshi capture) its own scheduling infrastructure
distinct from OPS-001's once-(or few-times)-daily baseline cadence. What
this task DID build and verify is the script's own idempotent "capture
whatever's due right now" logic on each invocation:

- `games_due_for_capture` computes each game's OWN due window from its own
  `game_start_timestamp` — never one shared slate-wide timestamp.
- `already_captured_game_pks` reads the existing `odds_books.jsonl` for the
  run date so a game already captured is never re-fetched/re-polled by a
  later invocation (satisfies "must not fetch Kalshi data for a game more
  than once close to first pitch").
- One Kalshi API call per invocation covers the whole slate (there is no
  cheaper per-game Kalshi endpoint); it is the due-window check, not the
  fetch, that gives each game its own near-first-pitch capture time across
  the day's many invocations.

Until a scheduler actually invokes this script repeatedly (OPS-001/OPS-003),
running it once manually only captures whichever games happen to be in the
window at that moment — this is expected and by design, not a bug, but it
means this task is genuinely not "fully done" in isolation, as the task file
warned.

**Near-first-pitch tolerance window (proposed default, needs user
confirmation before being treated as final product policy).** Documented in
`scripts/kalshi_pregame_capture.py` as `DEFAULT_WINDOW_START_MINUTES = 60.0`
/ `DEFAULT_WINDOW_END_MINUTES = 15.0`: a game becomes "due" once `now` falls
between 60 and 15 minutes before its own first pitch (both CLI-overridable
via `--window-start-minutes`/`--window-end-minutes`). Rationale: (1) 60
minutes is genuinely "close to game time," matching the task's framing of
Kalshi as a closing-style read rather than a morning snapshot; (2) the
15-minute floor leaves buffer so a scheduler running every 10-15 minutes
reliably lands at least one invocation inside the window, and a slightly
late run still captures a real pregame price instead of missing the window
once the game starts. This is a starting default per the task's own
instruction to propose-and-document rather than silently invent — worth
revisiting once real Kalshi MLB liquidity/timing behavior at that horizon is
observed.

**Design calls:**
- **Bid/ask midpoint as the per-side probability.** Per
  `docs/researcha/kalshi-integration.md` §3, a Kalshi yes price already is a
  probability; the midpoint of `yes_bid`/`yes_ask` is the standard bid/ask
  analog of stripping the spread to get one point estimate, reused instead
  of inventing a new normalization. A midpoint of exactly `0.0` or `1.0`
  (thin/empty book) is treated as "no usable price" and that game is skipped
  this run, not written with a degenerate price.
- **`snapshot_timestamp` is Kalshi's own latest per-side `updated_time`**
  (the more recent of the two matched markets), not this script's own
  wall-clock read time — same "provider's own last-update time" choice
  `odds_snapshots_for_schedule` already makes for The Odds API.
- **Point-in-time guard reuses `snapshot_is_pregame_valid` literally**, with
  `now` (this invocation's own clock) standing in for the missing
  "prediction_timestamp" concept this comparison-only data doesn't have —
  requires `latest_snapshot < now < first_pitch`. A script run that lands
  after first pitch (or a stale bronze row) is skipped for that game, not
  written as a misleading "pregame" read.
- **Bronze row -> raw-market-object shape reuses `_city_name_matches`
  and `match_kalshi_market` from `ingestion.kalshi.matching` directly**
  rather than re-deriving team-identity matching, including for assigning a
  matched market's bid/ask to home vs. away (Kalshi's bare city name, e.g.
  `"Seattle"`, needs the same word-subset containment check against this
  repo's full club names as the matcher itself uses — equality does not
  work here; caught by a failing test during implementation, see below).
- **A pre-PIPE-006 Bronze row with NULL `occurrence_datetime`** (from before
  this migration) is deliberately routed through `match_kalshi_market`'s own
  existing required-field validation (empty string in, `KalshiMatchingError`
  out) rather than a second explicit guard — one error path, not two.

**Files changed:**
- `scripts/kalshi_pregame_capture.py` (new)
- `tests/unit/scripts/test_kalshi_pregame_capture.py` (new)
- `src/ingestion/kalshi/snapshots.py` — additive: `occurrence_datetime`,
  `title`, `no_sub_title` are now required parse-time fields (matching the
  existing required-field style of `ticker`/`event_ticker`/etc.) and
  persisted as new nullable columns via `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` (so a pre-existing on-disk `bronze.kalshi_market_snapshots` table
  is migrated transparently, not recreated); the conflict-detection
  comparisons in `ingest_kalshi_market_snapshots` were extended to include
  the three new identity fields, consistent with how `event_ticker`/`side`
  are already treated as immutable per-key facts. No existing column,
  semantics, or DATA-022 test was changed.
- `tests/unit/ingestion/kalshi/test_kalshi_snapshots.py` — extended for the
  three new fields (round-trip + required-field-missing/malformed cases).
- `tests/integration/ingestion/kalshi/test_kalshi_ingestion.py` — added a
  round-trip test for the new columns and a dedicated backward-compatibility
  test that pre-creates the table in its pre-PIPE-006 shape (no new columns)
  and confirms a fresh ingest call migrates it additively without error.

**Commands run:**
- `python -m pytest tests/unit/ingestion/kalshi/ tests/integration/ingestion/kalshi/ tests/unit/scripts/ -q`
  → 128 passed, 1 xfailed.
- `python -m pytest -q` (full repo suite) → **863 passed, 5 xfailed** — same
  xfailed count as main pre-existing (DATA-022's price-precision P2,
  MARKET-003's `OverflowError` P2, and others already on main); no new
  failures, no regressions.

**Known limitations / open questions:**
- **Scheduler registration is out of scope, as agreed** — see the
  scheduling-mechanism section above. This script has not been run against
  a real live Kalshi payload during a real MLB slate; `--kalshi-json`
  offline replay was used for the integration test, mirroring
  `daily_predictions.py`'s own `--odds-json` replay pattern.
  `fetch_kalshi_payload` mirrors `fetch_odds_payload`'s HTTP-error handling
  but was not exercised against the live `api.elections.kalshi.com`
  endpoint in this task (network access was not attempted here); DATA-022's
  own handoff already confirmed that host/shape live, so this is a
  low-risk, not zero-risk, gap.
- **The 60/15-minute window is a proposed default, not a locked product
  decision** — flagged per the task's own instruction; confirm with the
  user before treating it as final, and consider whether it should differ
  for early-afternoon getaway games vs. locked-in night games.
- **No cross-market consistency check** (comparing team A's implied
  probability against `1 - team B`'s, per the research doc §3) is
  performed — each side's price is used independently via its own bid/ask
  midpoint, matching the research doc's finding that Kalshi's two per-team
  markets are not guaranteed to sum to 1 and a full consistency check was
  out of this task's scope.
- **This script imports the whole `daily_predictions.py` module** (via
  `import daily_predictions as dp`) purely to reuse `append_jsonl_records`,
  `_dict_rows`, `_utc_instant`, `parse_date`, and `parse_timestamp` without
  forking a second implementation, per the task's explicit "don't fork a
  fourth writer" instruction. This pulls in `daily_predictions.py`'s own
  heavy, unrelated dependency graph (xgboost, sklearn, the full training
  pipeline) as a load-time cost for a script that itself needs none of
  that. This is a real but pre-existing coupling problem inherent to
  `daily_predictions.py` being a monolithic script rather than a package
  with a separable "shared operator utilities" module; splitting that out
  is a legitimate future cleanup but was out of this task's allowed-files
  scope (`daily_predictions.py` is listed "Do not modify" except for its
  writer's *call site*, not a refactor target).
- **No liquidity/spread-width heuristic beyond the 0/1 boundary** — a very
  wide but technically valid bid/ask spread (e.g. `0.10`/`0.90`) still
  produces a midpoint price; only a fully empty side (bid == ask == 0, or
  the reciprocal) is treated as unusable. This matches the research doc's
  framing of MLB Kalshi liquidity as generally thin but did not add a
  configurable minimum-liquidity threshold, since none was specified in the
  task.

**ADR/state update:** no ADR needed (additive schema change + a new,
independent comparison-only script; no accepted decision changed).
`state/CURRENT.md` should get a short entry noting PIPE-006 implemented,
scheduler registration still pending OPS-001/OPS-003 — left to the
orchestrator/reviewer per this repo's normal flow, consistent with how
DATA-022/DATA-023 handoffs treated that update.

### Re-review fix (2026-08-15)

A Reviewer and a Tester independently converged on the same real P1 defect
(commit `e5dc545`, plus Tester commit `11de103` adding the failing
regression test): cross-game failure isolation was broken at two choke
points in `_run_capture`, both the same "one bad record blocks the whole
batch" pattern the task's own acceptance criteria explicitly forbid.

**Fix 1 (Reviewer/Tester P1) — batch Kalshi ingest.**
`ingest_kalshi_market_snapshots` (`src/ingestion/kalshi/snapshots.py`,
DATA-022's code, contract intentionally left unchanged) parses the ENTIRE
payload before any DB write and raises `KalshiDataError` on the FIRST
malformed market anywhere in `payload["markets"]`. `_run_capture`'s blanket
`except KalshiDataError: return 0` therefore aborted Kalshi capture for
every due game in the slate, not just the game whose market was bad — if a
bad record persisted across polls it would silently block the whole day's
Kalshi capture. Fixed in `scripts/kalshi_pregame_capture.py` by adding
`_valid_kalshi_markets(payload)`, which validates each market individually
by reusing `parse_kalshi_market_snapshots` on a single-market sub-payload
(so validity agrees exactly with what would fail at real ingest — no
re-derived validation logic) and returns only the well-formed markets.
`_run_capture` now passes `{"markets": valid_markets}` to
`ingest_kalshi_market_snapshots` instead of the raw payload; a genuinely
malformed top-level shape (not a dict, or `markets` not a list) still falls
through to `ingest_kalshi_market_snapshots`'s own top-level error and the
existing "payload malformed, skipping this run" behavior, unchanged.

**Fix 2 (Reviewer P2->confirmed real, same class) — candidate building.**
`candidates = kalshi_game_candidates_from_schedule(schedule)` was not
wrapped in a try/except. `kalshi_game_candidates_from_schedule` raises
`KalshiMatchingError` for the WHOLE schedule (its own fail-fast loop) if any
single game's `source_game_json` is missing the expected team-name shape,
which propagated uncaught through `main()` and crashed the entire
invocation with zero games captured. Fixed by adding
`_build_kalshi_candidates(schedule)`, which builds candidates one game at a
time (calling `kalshi_game_candidates_from_schedule([game])` per game) and
skips/logs just the offending `game_pk` on `KalshiMatchingError`, mirroring
the per-market isolation already used in `build_kalshi_capture_records`.

**Why this actually fixes the isolation property, not just the named
tests:** both fixes move the failure boundary from "whole schedule/payload"
to "one record" by validating/building per-item instead of per-batch, using
the SAME underlying validation code (`parse_kalshi_market_snapshots`,
`kalshi_game_candidates_from_schedule`) just invoked on singleton inputs —
so a single call's success/failure is provably identical to what the
batched call would have decided for that one item, and one item's failure
can no longer raise before any other item is processed. This holds for any
number of concurrently-malformed markets/games, not just the one-bad-record
cases the tests exercise (each bad item is independently caught and
skipped in its own loop iteration; nothing upstream of the loop can abort
it).

**Test evidence.**
- `tests/unit/scripts/test_kalshi_pregame_capture.py::test_main_one_malformed_market_blocks_capture_for_every_other_due_game`
  — before: FAILED (`AssertionError: one malformed, unrelated Kalshi market
  blocked Bronze ingestion for the whole batch...`); after: **PASSED**.
- Added
  `test_main_one_malformed_schedule_game_does_not_block_capture_for_other_games`
  (analogous regression test for fix 2, one game with `source_game_json={}`
  alongside a healthy due game) — **PASSED** from first write (confirms the
  fix, no separate before/after needed since this is a new test written
  against the fixed code).
- `python -m pytest tests/unit/scripts/test_kalshi_pregame_capture.py -q` →
  **23 passed** (was 22 passed, 1 failed before this fix).
- `python -m pytest tests/unit/ingestion/kalshi/ tests/integration/ingestion/kalshi/ tests/unit/scripts/test_kalshi_pregame_capture.py -q`
  → **102 passed, 1 xfailed** (the 1 xfail is DATA-022's pre-existing
  price-precision P2, unrelated to this fix; no new xfails needed for
  either fix, as required).
- `python -m pytest -q` (full repo suite) → **893 passed, 6 xfailed**, no
  failures, no regressions.

**Files changed:** `scripts/kalshi_pregame_capture.py` (added
`_valid_kalshi_markets`, `_build_kalshi_candidates`; `_run_capture` now
calls both instead of the raw batch functions),
`tests/unit/scripts/test_kalshi_pregame_capture.py` (new regression test
for fix 2). `src/ingestion/kalshi/snapshots.py` was intentionally NOT
modified — its own fail-fast-on-malformed-market contract for a single
ingest call is a pinned, correct design choice; the isolation fix lives
entirely at this script's own batch-orchestration boundary, which is where
the task said it belonged.

Out of scope for this round, untouched per instruction: the Reviewer's P2
(heavy `daily_predictions` module import for reuse) and P3s (private-
function cross-module import naming, tolerance-window default).
