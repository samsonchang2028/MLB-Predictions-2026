# APP-012 — Show Kalshi in the Odds-by-Book Comparison

## Status

implemented (candidate for review/test gates)

## Dependencies

- PIPE-006
- APP-005 (the existing odds-by-book table this extends)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `no` (small display-layer task, likely near-zero new code
if PIPE-006/MARKET-003 land Kalshi data in the existing `odds_books.jsonl`
schema as designed)

## Goal

Confirm/finish Kalshi showing up correctly in the Game Detail page's existing
odds-by-bookmaker comparison table (`src/app/game_detail.py`'s
`_load_odds_books`, rendered in `src/app/game_detail_page.py`). Because
PIPE-006 is designed to write Kalshi rows into the exact same
`odds_books.jsonl` schema sportsbook rows already use (via MARKET-003's
probability→American-odds conversion), this task should mostly be
**verification and minor labeling polish**, not new data-loading code — if it
turns out to need substantial new code, that's a signal PIPE-006/MARKET-003's
schema-reuse design didn't actually work cleanly, and worth flagging back
rather than working around here.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/app/game_detail.py` (`_load_odds_books`, `_best_price_for_side` —
  confirm these already work unmodified against a Kalshi row once PIPE-006
  writes one)
- `src/app/game_detail_page.py` (the odds-by-book table rendering, the
  verdict banner's "best price across all books" line — confirm Kalshi is
  correctly eligible to win "best price" the same as any sportsbook)

## Allowed files

- `src/app/game_detail_page.py` (labeling/display polish only, e.g. a
  human-readable "Kalshi" label instead of a raw `bookmaker` string if
  needed — check what PIPE-006 actually stores in the `bookmaker` field
  first)
- `tests/unit/app/test_app_game_detail.py`

## May modify if necessary

- `src/app/game_detail.py` only if verification reveals `_load_odds_books`/
  `_best_price_for_side` genuinely don't handle a Kalshi row correctly (e.g.
  if Kalshi's converted American price needs different rounding/display
  precision) — document why if this file needs touching, since the goal was
  zero changes here.

## Do not modify

- The verdict banner's edge/PLAY-PASS logic (`app.board.DEFAULT_EDGE_THRESHOLD`
  usage) — Kalshi is comparison-only, does not affect the canonical verdict,
  same boundary every other book already respects.

## Inputs

- `state/predictions/odds_books.jsonl` rows with `bookmaker: "kalshi"`
  (from PIPE-006)

## Outputs

- Kalshi appears as a normal row in the existing odds-by-book table, and is
  correctly eligible for the "best price found" callout in the verdict
  banner when it genuinely offers the best price for the model's favored
  side.

## Requirements

- No new table, no separate "Kalshi section" — it belongs in the same
  comparison table as every sportsbook, since the whole point (per the
  user's request) is "a wide range from DraftKings and stuff like that,
  other books, and plus Kalshi" as one unified comparison, not a separate
  display.
- If Kalshi's `snapshot_timestamp` is meaningfully different in character
  from sportsbook snapshots (per-game near-first-pitch vs. one shared daily
  batch time, per PIPE-006), confirm the existing `snapshot_pacific` display
  column still reads sensibly — it should, since it's already per-row, just
  verify.

## Critical correctness constraints

- None beyond what APP-005 already established (no DuckDB access, artifact-
  backed, works identically locally and on the deployed Streamlit Cloud app).

## Acceptance criteria

- A synthetic/fixture `odds_books.jsonl` row with `bookmaker: "kalshi"`
  renders correctly in the odds-by-book table and is correctly considered
  for the best-price verdict callout, verified via the same
  `streamlit.testing.v1.AppTest` headless approach APP-005 used.

## Required tests

- unit: `test_app_game_detail.py` case with a Kalshi row mixed in among
  sportsbook rows, confirming correct implied-probability computation and
  correct best-price selection when Kalshi does/doesn't offer the best price

## Handoff

**Summary.** Verification-only, as the task predicted. Read `_load_odds_books`
and `_best_price_for_side` in `src/app/game_detail.py`: both are already
generic over `bookmaker` — they read `home_american`/`away_american`/
`snapshot_timestamp`/`bookmaker` off any `odds_books.jsonl` row with no
special-casing, so a Kalshi row (`bookmaker="kalshi"`, confirmed by reading
`scripts/kalshi_pregame_capture.py`'s `KALSHI_BOOKMAKER = "kalshi"` /
`_game_capture_record`, which writes `bookmaker`/`source` both as `"kalshi"`
and prices via `probability_to_american`) flows through unmodified: no-vig
probability computed, included in the odds-by-book table, and eligible to
win `_best_price_for_side`'s max-payout comparison for the model's favored
side exactly like any sportsbook row.

`src/app/game_detail_page.py`'s table already renders `book["bookmaker"]`
verbatim with no per-bookmaker capitalization/formatting (The Odds API's
own bookmaker keys — `"draftkings"`, `"fanduel"`, etc. — are already raw
lowercase strings in that column today). `"kalshi"` displays exactly as
consistently as every existing row, so the "human-readable label" polish
the task allowed for turned out to be unnecessary — adding capitalization
only for Kalshi would have made it inconsistent with the rest of the
column, not more polished.

**Files changed:**

- `tests/unit/app/test_app_game_detail.py` — two new cases: a Kalshi row
  mixed in with a sportsbook row where Kalshi offers the better price for
  the model's favored side (wins `best_price`, implied probability/
  `snapshot_pacific` computed correctly off its own near-first-pitch
  timestamp), and the inverse (Kalshi present but a sportsbook has the
  better price, so Kalshi correctly does not win).
- `src/app/game_detail.py` — **not modified.** Confirmed correct by
  inspection and by the new tests; PIPE-006/MARKET-003's schema-reuse
  design worked exactly as intended.
- `src/app/game_detail_page.py` — **not modified.** No labeling polish was
  needed (see above).

This is the "near-zero new code" outcome the task predicted, not a signal
of PIPE-006/MARKET-003's design falling short.

**Commands run:**

- `python -m pytest tests/unit/app/test_app_game_detail.py -q` → 12 passed.
- `python -m pytest -q` (full suite) → 895 passed, 6 xfailed (all
  pre-existing; matches main's pre-task xfail count from the PIPE-006
  handoff, no new xfails introduced).
- Ad hoc `streamlit.testing.v1.AppTest` headless smoke run (not committed,
  same approach APP-005 used) against a synthetic `daily.jsonl`/
  `odds_books.jsonl` pair with one Kalshi row and one DraftKings row:
  zero exceptions; the rendered odds-by-book dataframe shows both rows
  (`kalshi` at `-110`, `draftkings` at `-150`, each with its own correctly
  formatted Pacific snapshot time — Kalshi's near-first-pitch 22:45 UTC
  vs. DraftKings' 14:00 UTC daily-batch time, both read sensibly); the
  verdict banner's success message correctly reads "Best price found for
  NYY: kalshi -110." confirming Kalshi wins the verdict-banner callout
  when it genuinely offers the best price. Synthetic fixtures were
  temp-directory-scoped and not committed.

**Test results:** all green, no regressions.

**Known limitations:** none beyond APP-005's own known limitations (no
literal browser click-through this session). No new ADR or
`state/CURRENT.md` policy change required — this is a display-only
verification task with no methodology impact; `state/CURRENT.md` should
be updated by the orchestrator once this clears review/test gates, per
repo convention.
