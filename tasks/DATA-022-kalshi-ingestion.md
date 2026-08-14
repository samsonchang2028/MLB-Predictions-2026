# DATA-022 — Kalshi Market Data Ingestion

## Status

implemented, awaiting review

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

### Implementer handoff (2026-08-14)

**Summary.** Added `src/ingestion/kalshi/snapshots.py`
(`parse_kalshi_market_snapshots` + `ingest_kalshi_market_snapshots`) and
`src/ingestion/kalshi/__init__.py`, mirroring
`src/ingestion/odds/snapshots.py`'s parse-function + idempotent-DuckDB-ingest
pattern for a new, independent `bronze.kalshi_market_snapshots` table.
`src/ingestion/odds/` was not touched.

**Response shape: verified live, not assumed.** `api.kalshi.com` (the host
named in the research doc) does not resolve from this environment. The
actual working REST host is **`api.elections.kalshi.com`**
(`https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open`)
— confirmed by fetching it directly and getting real, current (2026-08-16/17)
MLB `KXMLBGAME` markets (Seattle @ Houston, Colorado @ San Francisco,
Milwaukee @ LA Dodgers). This is a genuine finding beyond the research doc
and matters for whoever wires a real fetcher (DATA-023/PIPE-006): use
`api.elections.kalshi.com`, not `api.kalshi.com`.

Confirmed live field names on each `Market` object (exact, from the raw
response, not summarized): `ticker`, `event_ticker`, `yes_sub_title`,
`no_sub_title`, `status`, `result`, `yes_bid_dollars`, `yes_ask_dollars`,
`no_bid_dollars`, `no_ask_dollars`, `last_price_dollars`, `updated_time`,
`open_time`, `close_time`, `occurrence_datetime`, plus many fields not needed
for this table (`price_ranges`, `custom_strike`, `rules_primary`, etc.). Two
things the research doc did not pin down that the live payload confirmed:
(1) all four price fields are present per market (not just yes_bid/yes_ask) —
so the table stores all four; (2) prices are decimal-dollar **strings**
(e.g. `"0.4400"`), not floats or cents — parsed with `Decimal`, not `float`,
to avoid binary rounding on a money-shaped value.

**Design calls (documented per AGENTS.md's "smallest reasonable judgment
call" guidance):**

- `side` = `yes_sub_title` (Kalshi's own docs call this "the yes side of
  this market" — i.e. the team this market's "yes" resolves for). This is a
  direct field, not a ticker-suffix parse, per Kalshi's own guidance not to
  parse ticker strings.
- `source_payload_sha256` is a SHA-256 of the canonical (sorted-key) JSON
  serialization of the already-decoded `payload` dict passed into
  `parse_kalshi_market_snapshots`, not a hash of literal wire bytes. This
  matches the odds-ingestion mirror's function signature
  (`parse_the_odds_api_moneylines(payload, ...)` takes decoded JSON, not raw
  bytes) — a bytes-hashing raw-file-on-disk design like
  `bronze.mlb_game_detail_payloads` would need a different (bytes-accepting)
  signature the task did not ask for. One hash is computed per call and
  shared across every row that call produces, same relationship as
  `mlb_game_detail_payloads.payload_sha256` to its downstream rows.
- `yes_bid`/`yes_ask`/`no_bid`/`no_ask` stored as `DECIMAL(5,4)` with a
  `CHECK (0 <= x <= 1)` — a `"0.0000"` bid is treated as a normal "no current
  bid" observation (MLB liquidity on Kalshi is thin per the research doc),
  not malformed data; only out-of-range or non-numeric values are rejected.
- Conflict/idempotency key: `(source, market_ticker, snapshot_timestamp)`,
  using Kalshi's own `updated_time` as `snapshot_timestamp` (the provider-
  supplied "as of" time), same role `last_update` plays for a sportsbook in
  the odds mirror. A repeat fetch with identical prices is a no-op; a
  different price for the same key (in the same payload, or against an
  already-stored row) raises `KalshiDataError`.

**Files changed:**
- `src/ingestion/kalshi/snapshots.py` (new)
- `src/ingestion/kalshi/__init__.py` (new)
- `tests/unit/ingestion/kalshi/test_kalshi_snapshots.py` (new)
- `tests/unit/ingestion/kalshi/fixtures/kalshi_market_snapshots.json` (new —
  built from the real live 2-market Seattle-vs-Houston payload captured
  during this task, trimmed to the fields the parser reads)
- `tests/integration/ingestion/kalshi/test_kalshi_ingestion.py` (new)

**Naming deviation from the task's suggested test paths:** the task listed
`tests/unit/ingestion/kalshi/test_snapshots.py` and implied
`tests/integration/ingestion/kalshi/test_ingestion.py`-style naming. Using
those exact basenames collides with `tests/unit/ingestion/odds/test_snapshots.py`
and `tests/integration/ingestion/odds/test_ingestion.py` under pytest's
default rootdir-relative import mode (no `__init__.py` anywhere under
`tests/`), which aborts the *entire* suite's collection with `import file
mismatch`. Renamed to `test_kalshi_snapshots.py` /
`test_kalshi_ingestion.py` (same pattern already used elsewhere in this repo,
e.g. `test_schedule_ingestion.py` under `tests/integration/ingestion/mlb/`)
rather than adding `__init__.py` files outside this task's allowed
directories.

**Commands run:**
- `python -m pytest tests/unit/ingestion/kalshi tests/integration/ingestion/kalshi -q`
  → 28 passed.
- `python -m pytest -q` (full repo suite, from the worktree, using
  `../predictions-1/.venv/Scripts/python.exe`) → **699 passed**.

**Known limitations:**
- No fetcher/HTTP client was added — this task is parse+store only, per the
  task's own scope. DATA-023 (game_pk matching) and PIPE-006 (fetch cadence,
  wiring into the daily pipeline) are separate, undone.
- Only `GET /markets` (top-of-book summary) is modeled, not
  `GET /markets/{ticker}/orderbook` (full depth). The research doc and this
  task's Outputs section both point at the summary endpoint as "likely the
  right endpoint for a snapshot ingester"; full depth was out of scope.
- Cross-market consistency (yes-team-A vs. `1 - yes`-team-B, per the research
  doc §3) is not checked here — it is a market-analysis concern for
  MARKET-003, not an ingestion-time validation; Kalshi's own two per-team
  markets are independently stored and independently validated.
- Rate limits, ToS/usage-restriction terms, and state-availability geofencing
  for read-only market data (flagged as unverified in the research doc §5)
  were not independently re-checked in this pass; this task only performs a
  handful of read requests, well under any plausible limit.
- `state/predictions/` shows as untracked in this worktree's `git status`;
  it predates this task's changes and was left untouched (not part of the
  Kalshi diff, outside the allowed-files list).

**ADR/state update:** no ADR is needed (the research doc already flagged
Kalshi as a structurally distinct source, not a variant of the sportsbook
odds engine, and this task changes no accepted decision). `state/CURRENT.md`
should get a one-line "DATA-022 implemented, awaiting review" entry when this
task is merged — left to the orchestrator/reviewer per this repo's normal
flow, not written by the implementer.
