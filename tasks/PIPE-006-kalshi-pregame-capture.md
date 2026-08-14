# PIPE-006 — Kalshi Pregame Capture (Near First Pitch, Not Daily-Batch)

## Status

backlog

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

## Allowed files

- `scripts/daily_predictions.py` (or a new small dedicated script, e.g.
  `scripts/kalshi_pregame_capture.py`, if the near-first-pitch scheduling
  shape doesn't fit naturally as a code path inside the existing once-daily
  script — this is an open design question for the implementer to resolve
  and document, not decided here)
- `tests/unit/scripts/` (matching whichever file above)

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
