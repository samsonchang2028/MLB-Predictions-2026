# MARKET-003 — Probability ↔ American Odds Conversion Helper

## Status

backlog

## Dependencies

- MARKET-001

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `no` (single small function + tests, one file, low risk)

## Goal

Add the one missing direction of odds conversion this repo doesn't have yet:
probability → American odds (the inverse of the existing
`american_to_decimal`/`american_to_implied_probability`). This exists purely
so Kalshi's already-a-probability prices (a Kalshi yes/no contract at 62¢ IS
a probability, no conversion needed to interpret it) can be stored and
displayed through the exact same `odds_books.jsonl` schema and
`src/app/game_detail.py` display logic that already handles sportsbook
American-odds prices — reuse, not a parallel display path for Kalshi.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/market/engine.py` (existing `american_to_decimal`,
  `american_to_implied_probability`, `decimal_to_implied_probability`,
  `_validate_american`, `_validate_probability` — this task adds the missing
  inverse next to these, following their exact validation/error-message
  style)
- `docs/researcha/kalshi-integration.md` (§ on why `no_vig_two_way` doesn't
  port directly to Kalshi's per-side independent market structure — this
  task deliberately does NOT try to generalize `no_vig_two_way`; it adds a
  narrow, separate conversion instead)

## Allowed files

- `src/market/engine.py`
- `tests/unit/market/test_engine.py` (or wherever the existing engine tests
  live — confirm exact path before creating a new one)

## May modify if necessary

- none

## Do not modify

- `no_vig_two_way`, `evaluate_pregame`, `evaluate_benchmark` and everything
  else in this file — purely additive, no existing function's behavior
  changes

## Inputs

- A probability in [0, 1] (from Kalshi's yes-price-as-probability, or a
  bid/ask midpoint computed by DATA-022/PIPE-006 before this function is
  called — spread-handling is upstream of this function, not this
  function's job)

## Outputs

- `probability_to_american(probability: float) -> int` in `src/market/engine.py`
  — the exact inverse of `american_to_implied_probability`, rounding to the
  nearest valid American price (must never produce a value with
  `abs(american) < 100`, matching `_validate_american`'s existing floor —
  decide and document the rounding/edge-case behavior right at the ±100
  boundary explicitly, don't leave it implicit).

## Requirements

- `probability_to_american(american_to_implied_probability(x))` must
  round-trip to `x` (or the nearest valid integer American price to `x`) for
  the full valid input range — add this as an explicit property-style test,
  not just a couple of hand-picked examples.
- Reject `probability <= 0` or `probability >= 1` the same way
  `_validate_probability` already validates its inputs elsewhere in this
  file (probabilities of exactly 0 or 1 have no finite American-odds
  equivalent).
- Reuse `_validate_probability` for input validation rather than writing a
  second copy of the same check.

## Critical correctness constraints

- This function must not be used anywhere in the canonical prediction path
  (`daily.jsonl`, `market_probability`, `edge`) — it exists solely for
  display-layer reuse of comparison-book data (Kalshi via PIPE-006/APP-012),
  same non-negotiable boundary PIPE-004 already established for other
  bookmakers.

## Acceptance criteria

- Round-trip property holds across the valid probability range (test at
  minimum: 0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99, plus the favorite/underdog
  boundary at 0.5).
- Existing `src/market/engine.py` tests still pass unmodified.

## Required tests

- unit: round-trip property test, boundary/invalid-input rejection tests,
  mirroring the existing style of `american_to_decimal`'s own test coverage

## Handoff

Record: summary, files changed, commands run, test results, known
limitations, any new ADR/state changes (none expected — this is additive
utility code, not a methodology change).
