# DATA-022 — Kalshi Market Data Ingestion

## Status

backlog

## Dependencies

- DATA-001

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

## Goal

Ingest Kalshi's MLB per-game yes/no event-contract prices as a new, independent
odds source alongside The Odds API sportsbook lines — immutable, timestamped,
append-only, mirroring how `src/ingestion/odds/snapshots.py` already treats
sportsbook moneylines. This task is fetch-and-store only; mapping a Kalshi
event to this repo's canonical `game_pk` is DATA-023, and deciding *when* to
fetch (once per game, near first pitch, not on the once-daily sportsbook
cadence) is PIPE-006. This task's own fetches may run on whatever cadence is
convenient for development/testing — the near-first-pitch requirement belongs
to PIPE-006's scheduling logic, not to this ingestion function's own timing.

See `docs/researcha/kalshi-integration.md` for the research this is based on
— read it first, it has the concrete API shape, MLB series ticker, and
structural differences from The Odds API already investigated.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/researcha/kalshi-integration.md` (background research: confirms
  Kalshi's MLB coverage under series ticker `KXMLBGAME`, that public market
  data needs no authentication, and the per-game structure)
- `src/ingestion/odds/snapshots.py` (the pattern this mirrors: parse function
  + idempotent DuckDB ingestion function, immutable primary-key-based inserts)
- `src/ingestion/odds/__init__.py` (export shape to match)

## Allowed files

- `src/ingestion/kalshi/` (new: `__init__.py`, `snapshots.py`)
- `tests/unit/ingestion/kalshi/`
- `tests/integration/ingestion/kalshi/`

## May modify if necessary

- none

## Do not modify

- `src/ingestion/odds/` (existing sportsbook ingestion is untouched — Kalshi
  is a structurally distinct source, not a variant of the odds-API parser)
- `scripts/daily_predictions.py` (wiring into the daily pipeline is PIPE-006)

## Inputs

- Kalshi's public market-data REST API (no auth required for reads, per the
  research doc — confirm current endpoint paths/response shape against the
  live API before writing the parser, the research doc's shape is
  medium-confidence, not verified against a live response)
- MLB game series ticker `KXMLBGAME` (or whatever the live API confirms as
  current — Kalshi ticker conventions can change season to season)

## Outputs

- `bronze.kalshi_market_snapshots` — new DuckDB table, immutable append-only,
  mirroring `bronze.odds_moneyline_snapshots`'s shape but for Kalshi's actual
  fields: `market_ticker`, `event_ticker`, `side` (Kalshi quotes yes/no per
  team-market, not a single home/away pair the way a sportsbook does — model
  this honestly, do not force it into the sportsbook table's exact columns),
  `yes_bid`, `yes_ask` (or however the live API actually shapes price data —
  confirm, don't assume a single "price" field), `snapshot_timestamp`,
  `source_payload_sha256` (matches this repo's existing raw-payload integrity
  pattern in `bronze.mlb_game_detail_payloads`).
- `src/ingestion/kalshi/snapshots.py`: `parse_kalshi_market_snapshots(payload,
  ...)` and `ingest_kalshi_market_snapshots(connection, payload, ...)`,
  matching `parse_the_odds_api_moneylines`/`ingest_the_odds_api_moneylines`'s
  signatures and idempotency contract as closely as the actual data shape
  allows.

## Requirements

- Raw API responses are immutable (repo-wide rule) — store the raw JSON
  payload's hash the same way `bronze.mlb_game_detail_payloads` does, don't
  discard it after parsing.
- Ingestion is idempotent — identical re-fetch of the same market/timestamp
  is a no-op, not a duplicate row; a genuine conflict (same key, different
  price) raises, same contract as `ingest_the_odds_api_moneylines`.
- Do not assume Kalshi's response shape from the research doc alone — that
  doc flags itself as sourced from public docs/third-party mentions, not a
  verified live payload. Fetch one real response during implementation and
  confirm field names before finalizing the parser.
- Every observation preserves its source and timestamp (repo-wide rule,
  same as sportsbook odds).

## Critical correctness constraints

- This is a NEW, independent Bronze source. It must not touch, alias, or be
  confused with `bronze.odds_moneyline_snapshots` — Kalshi and sportsbook
  odds are different market structures (see `docs/researcha/kalshi-integration.md`
  §"structural difference") and must stay in their own table until a later
  task (MARKET-003) deliberately normalizes them into a comparable shape.

## Acceptance criteria

- A saved/fixture Kalshi API response for at least one real MLB game parses
  into the expected row shape.
- Re-ingesting the identical payload twice inserts no duplicate rows.
- A payload with a genuinely changed price for the same market/timestamp key
  raises rather than silently overwriting.

## Required tests

- unit: parser against fixture payload(s), including a malformed/incomplete
  payload case (missing price field, missing ticker, etc. — mirror
  `tests/unit/ingestion/odds/test_snapshots.py`'s coverage style)
- integration: ingestion round-trip against a real (or realistic fixture)
  DuckDB connection, idempotency + conflict-detection cases

## Handoff

Record: summary, files changed, commands run, test results, known
limitations (especially: was the response shape verified against a live
Kalshi API call, or only against the research doc's medium-confidence
description?), any new ADR/state changes.
