# MARKET-003 — Probability to American-odds conversion

## Status

`candidate` — implementer-complete, awaiting review.

## Dependencies

- MARKET-001 (market probability and edge engine; this task extends `src/market/engine.py`)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

## Goal

Add `probability_to_american(probability: float) -> int` to `src/market/engine.py` — the missing
inverse of the existing `american_to_implied_probability`. This exists so Kalshi's already-a-
probability prices (a future integration, not part of this task) can be converted to American-
odds-equivalent values and stored/displayed through the existing odds comparison schema
unchanged. This task is standalone utility code — it does not touch Kalshi, ingestion, or any
pipeline wiring.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/market/engine.py` (module docstring documents the odds formulas and provenance rules)

## Allowed files

- `src/market/engine.py`
- `tests/unit/market/test_market_engine.py`
- `src/market/__init__.py` (public export, required for the function to be usable/importable
  the same way every other engine function is — not listed in the original task's "allowed
  files" but necessary for consistency; see Handoff)
- `tasks/MARKET-003-probability-to-american-odds.md` (this file)

## May modify if necessary

- None.

## Do not modify

- `no_vig_two_way`, `evaluate_pregame`, `evaluate_benchmark`, or any other existing function's
  behavior — purely additive.

## Inputs

- A win probability in `(0, 1)`.

## Outputs

- `probability_to_american(probability: object) -> int` in `src/market/engine.py`, exported from
  `src/market/__init__.py`.

## Requirements

- `probability_to_american(probability)` is the mathematical inverse of
  `american_to_implied_probability`: round-trip
  `probability_to_american(american_to_implied_probability(x))` returns `x` (or the nearest
  valid integer American price to `x`) across the full valid range.
- Reuses the existing `_validate_probability` helper for input validation (no duplicate check).
- Rejects `probability <= 0` or `probability >= 1` (no finite American-odds equivalent at those
  bounds), in the same `ValueError` style as `_validate_probability`.
- The result never violates `_validate_american`'s floor (`abs(american) < 100` is invalid).
- Follows the module's existing docstring/code style (formula explanation added to the module
  docstring in the same style as the existing American/decimal/probability formulas).

## Critical correctness constraints

- No behavior change to any existing function (`no_vig_two_way`, `evaluate_pregame`,
  `evaluate_benchmark`, `american_to_decimal`, `american_to_implied_probability`,
  `decimal_to_implied_probability`, validation helpers).
- This module remains DB/network-free and side-effect-free (unchanged).

## Acceptance criteria

- Round-trip property holds across the valid probability range (tested at 0.01, 0.1, 0.3, 0.5,
  0.7, 0.9, 0.99, plus explicit boundary behavior at 0.5).
- All existing tests in `tests/unit/market/test_market_engine.py` still pass unmodified.

## Required tests

- unit: hand-computed known-value conversions (mirroring the existing
  `american_to_implied_probability` known-value tests), round-trip property tests, explicit
  0.5-boundary behavior, invalid-input rejection (`<= 0`, `>= 1`, non-numeric, bool, `None`), and
  a floor-never-violated check.

## Handoff

**Summary.** Added `probability_to_american(probability: object) -> int` to
`src/market/engine.py`, directly below `decimal_to_implied_probability` (next to the other
American/decimal/probability conversion functions). It is the inverse of
`american_to_implied_probability`:

- favorite (`probability >= 0.5`): `american = -100 * probability / (1 - probability)`
- underdog (`probability < 0.5`): `american = 100 * (1 - probability) / probability`

Result is rounded to the nearest integer, then passed through the existing `_validate_american`
helper as a defensive final check (belt-and-suspenders: the formula's minimum magnitude is
provably exactly `100` at `probability == 0.5` and grows monotonically away from it, so this
should never actually raise, but reuses the existing floor check rather than re-deriving the
guarantee by inspection alone).

Validation reuses `_validate_probability` (numeric/bool/range check), then adds one explicit
open-interval check (`probability <= 0.0 or probability >= 1.0`) matching its error-message
style, since `_validate_probability` itself allows the closed interval `[0, 1]` and this
function's math has no finite answer at the endpoints.

**0.5 boundary decision.** At `probability == 0.5` the theoretical American price is exactly
`+100` or `-100` (both imply 0.5, mirroring `american_to_implied_probability`'s existing
behavior). A single function can only return one value, so `probability_to_american(0.5)`
returns `-100` (favorite-side convention), documented in both the module docstring and the
function docstring. Consequence: `probability_to_american(american_to_implied_probability(100))`
returns `-100`, not `100` — this is the "nearest valid integer American price" case the task
spec anticipated, not a bug. `probability_to_american(american_to_implied_probability(-100))`
does round-trip exactly to `-100`.

**Files changed:**

- `src/market/engine.py` — added `probability_to_american`; added its formula to the module
  docstring. No other function's code changed.
- `src/market/__init__.py` — added `probability_to_american` to the import and `__all__` list
  (not in the task's originally listed allowed files, but required so the new function is
  importable from `market` the same way every sibling function already is; every other function
  in this module is re-exported here, so leaving this one out would be an inconsistent,
  surprising omission for any caller, including the future Kalshi integration this task exists
  to unblock).
- `tests/unit/market/test_market_engine.py` — added a "Probability -> American odds" section:
  hand-verified known-value tests (using the same values as the existing
  `american_to_implied_probability` known-value tests, inverted), an integer-return-type check,
  round-trip tests keyed off real American prices (`-110, 110, -200, 150, -100`), a dedicated
  `+100` boundary round-trip test documenting the asymmetric behavior above, a
  probability-range round-trip property test (`0.01` through `0.99`, `abs=5e-3` tolerance to
  account for integer rounding), invalid-input rejection tests (`0`, `1`, out-of-range, `None`,
  string, bool), and a floor-never-violated check.

**Commands run:**

- `../predictions-1/.venv/Scripts/python.exe -m pytest tests/unit/market/test_market_engine.py -q`
  → 86 passed.
- `../predictions-1/.venv/Scripts/python.exe -m pytest -q` (full repo suite) → 701 passed.

**Pass/fail status:** All tests pass; no existing test was modified.

**Known limitations:**

- Rounding uses Python's built-in `round()` (banker's rounding, round-half-to-even) for the rare
  case where the theoretical American price lands exactly on `x.5`. Not expected to matter in
  practice (odds are quoted in whole numbers; the round-trip tests use `abs=5e-3` tolerance, not
  exact equality, for this reason) but noted for anyone hand-verifying a specific edge value.
- No Kalshi-specific integration, storage, or display wiring was added — this task is
  intentionally standalone utility code per the task spec.

**Reviewer/tester follow-ups:** None known. Recommend the reviewer specifically check the 0.5
boundary convention (favorite-side `-100`) is an acceptable judgment call, since the original
task spec left it open ("decide/document explicit behavior").

**ADR/state update needed:** No ADR change — this is additive utility code with no methodology
or architecture impact. `state/CURRENT.md` should get a one-line entry once this is
reviewed/merged (not done here, since Status is `candidate`, not `done`/`approved`).
