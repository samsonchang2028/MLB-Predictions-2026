# DATA-024 — ingest totals (over/under) odds snapshots

## Status

`ready`

## Dependencies

- DATA-003 (live timestamped odds ingestion)
- DATA-004 (Silver odds contract)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` — branch `agent/DATA-024-totals-odds-ingestion`

## Goal

Extend the The Odds API parser to ingest **totals** (over/under runs) markets
alongside existing moneyline (`h2h`) parsing. Preserve timestamped, append-only
semantics and ADR-002 snapshot timing rules.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/ingestion/odds/snapshots.py`
- `src/transforms/silver.py` (odds normalization)
- `docs/researcha/totals-market.md` (ingestion section)
- `docs/decisions/ADR-002-point-in-time.md`

## Allowed files

- `src/ingestion/odds/snapshots.py`
- `src/ingestion/odds/__init__.py`
- `src/transforms/silver.py` (totals silver mapping only if required)
- `tests/unit/ingestion/odds/test_totals_snapshots.py` (new)
- `tests/fixtures/odds/` (new fixture JSON if needed)
- `tasks/DATA-024-totals-odds-ingestion.md`

## Do not modify

- Moneyline parser behavior for `h2h` (regression tests must still pass)
- `src/market/engine.py`
- `src/simulation/*`
- `scripts/daily_predictions.py` (SIM follow-up wires fetch)
- Streamlit

## Requirements

1. Parse `market.key == "totals"` outcomes with `name` Over/Under and shared
   `point` (the line, e.g. 8.5).
2. Row shape includes: `game_pk` mapping (reuse schedule matching from
   moneyline path), `bookmaker`, `line`/`point`, `side` (over/under),
   `american_odds`, `snapshot_timestamp`, `source`.
3. **Key uniqueness**: `(run_date or snapshot_ts, game_pk, bookmaker, market, line, side)`
   — multiple lines per book per game are allowed.
4. Do not break existing `parse_the_odds_api_moneylines` tests.
5. Fixture-based unit tests for: single line, multiple lines, missing point,
   malformed outcome names.
6. Document whether totals land in a new bronze table or extend existing with
   `market_type` discriminator — prefer smallest change that keeps moneyline
   idempotent.

## Acceptance criteria

- [ ] Totals parser function exported and tested
- [ ] Existing moneyline tests pass
- [ ] New totals unit tests pass
- [ ] ADR-002 timing fields preserved on each snapshot row
- [ ] Handoff documents Silver normalization gap if deferred to follow-up

## Handoff

Report parser API, table shape, tests run, and what SIM-002 / daily operator
need to consume totals lines.
