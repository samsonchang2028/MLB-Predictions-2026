# Research: Kalshi Integration (yes/no orderbook market alongside The Odds API)

Status: research only, no code changed. Written to inform a future task-graph
entry (`DATA-0XX` / `MARKET-00X`, see §6). Confidence is marked per claim;
Kalshi's live product surface for MLB shifts week to week, so anything
state-dependent (which games are listed, exact ticker format) should be
re-verified against the live API before implementation, not trusted from this
document alone.

## 1. What Kalshi offers for MLB today

Kalshi is a CFTC-regulated designated contract market (DCM) trading binary
event contracts, not a sportsbook. For MLB it lists several market families
under the `sports/baseball/mlb` category:

- **Per-game moneyline-equivalent markets** — series ticker `KXMLBGAME`.
  Confidence: **medium-high** (found via web search, including a live example
  URL `kalshi.com/markets_by_ticker/kxmlbgame-26jul182205wshath-ath`, and a
  third-party bot repo (`mmoore07129/mlb-kalshi-bot`) built specifically
  against it; not verified by fetching the live Kalshi API myself in this
  research pass). Reporting says KXMLBGAME was, at least at one point, the
  single most-traded market on the exchange (~11% of all Kalshi volume in one
  snapshot).
- **Season/futures markets** — World Series champion (`KXMLB`), AL/NL pennant,
  division winners, MVP (`KXMLBALMVP`, `KXMLBNLMVP`), "next team" trade-deadline
  markets, CBA/lockout markets. These are not per-game and are out of scope for
  a moneyline-comparison feature.
- Availability is explicitly **not guaranteed for every game or every state**
  — third-party coverage repeatedly notes MLB liquidity is thinner than
  NFL/NBA on Kalshi and listings vary day to day. Any ingestion module must
  treat "no Kalshi market for tonight's game" as a normal, expected case, not
  an error — same posture the repo already takes for `unmatched_events` in
  `odds_snapshots_for_schedule`.

**Ticker shape (confidence: medium, one example observed, Kalshi's own docs
warn "do not parse ticker strings to infer relationships"):**
`KXMLBGAME-26JUL182205WSHATH-ATH` decomposes as
`{series_ticker}-{event_ticker suffix: date+time+away+home abbrev}-{market
ticker suffix: one team's abbreviation}`. The important structural fact,
distinct from The Odds API: **Kalshi models a single game as an event
containing two separate single-sided binary markets** (e.g. `...-WSH` "will
Washington win" and `...-ATH` "will Athletics win"), not one two-outcome
market like The Odds API's `h2h` market with a `home`/`away` outcome pair.
The two per-game markets are complementary (yes on team A's market ≈ no on
team B's market) but are fetched/identified as two distinct ticker strings.
Kalshi's own docs say not to rely on parsing the ticker and instead resolve
identity through the `series_ticker` / `event_ticker` / `market` fields
returned by the API (`get-events`, `get-markets`).

## 2. Public API surface

Base URLs (confidence: medium, from third-party docs summaries, not fetched
directly):

- REST: `https://api.kalshi.com/trade-api/v2`
- WebSocket: `wss://api.kalshi.com/trade-api/ws/v2`
- Demo/sandbox environment also exists for testing.

**Authentication:** Confirmed via `docs.kalshi.com` (fetched directly).
Public market-data REST endpoints (`get-events`, `get-markets`,
`get-market-orderbook`, trades, candlesticks) require **no authentication at
all** — no API key, no account. Only trading/portfolio endpoints (placing
orders, balances, positions, fills) require an API-key pair plus a
per-request RSA-PSS signature (`KALSHI-ACCESS-KEY` / `-TIMESTAMP` /
`-SIGNATURE` headers) — no session token, no OAuth flow, no JWT refresh. This
is a materially different model from The Odds API's single static query-param
API key.

**Rate limits** (confidence: medium, third-party summary, not confirmed on
`docs.kalshi.com` directly): public read endpoints ~30 req/s; authenticated
write endpoints ~10 req/s, token-bucket based, tiered by account level. A
polling cadence of "once per pipeline run" (this repo's existing pattern for
The Odds API) is trivially inside this limit regardless.

**Cost:** Confidence high for "API access itself is free" (multiple
independent sources agree); Kalshi charges a small per-contract trading fee
(~$0.02/contract) only on executed trades, which is irrelevant to a read-only
market-data ingestion module.

**Endpoints relevant to ingestion** (confirmed via direct fetch of
`docs.kalshi.com/api-reference/market/get-markets` and
`.../get-market-orderbook`):

- `GET /markets` — filterable by `series_ticker`, `status`, etc. Each `Market`
  object already carries a **summarized top-of-book view** without needing
  the orderbook endpoint: `ticker`, `event_ticker`, `yes_bid_dollars`,
  `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars`, `last_price_dollars`,
  `volume_fp`, `open_interest_fp`, `status` (`initialized|inactive|active|
  closed|determined|disputed|amended|finalized`), `open_time`, `close_time`,
  `result` (`yes|no|scalar|""`). **This is likely the right endpoint for a
  snapshot ingester** — it gives bid/ask on both sides in one call per
  series, no need to walk the full order book for a simple probability
  snapshot.
- `GET /markets/{ticker}/orderbook` — full depth order book if deeper analysis
  is ever wanted. Returns `orderbook_fp.yes_dollars` / `orderbook_fp.no_dollars`,
  each an array of `[price_string, quantity_string]` levels, **bids only on
  both sides** (Kalshi does not return "asks" as a separate structure — a
  no-side ask at price P is definitionally a yes-side bid at `100-P` cents,
  so the two bid arrays are sufficient to reconstruct the full book). Prices
  are in **dollars as strings** (e.g. `"0.1500"`), not integer cents, despite
  colloquial "62 cents" framing — parsing must account for this.
- `GET /events`, `GET /events/{event_ticker}` — event-level grouping (a game),
  useful for resolving which two per-team markets belong to one game and for
  getting the game's human-readable title/subtitle for matching.

## 3. Price -> probability mapping (vs. this repo's existing no-vig engine)

**Key qualitative difference from `src/market/engine.py`:** a Kalshi yes
price is *already* an implied probability, no American-odds conversion layer
is needed at all.

- Sportsbook (`american_to_implied_probability` in `engine.py`): American
  price -> decimal odds -> `1/decimal`. Two coupled one-sided prices (home
  American, away American) that individually overround, requiring
  `no_vig_two_way` to strip the vig by proportional normalization so the pair
  sums to exactly 1.0.
- Kalshi: `yes_bid_dollars` (or `yes_ask_dollars`) on the "will team A win"
  market **is** a probability in `[0, 1]` directly (a price of `$0.62` = 62%
  implied). No decimal-odds intermediate step, no `american_to_decimal`
  equivalent needed.

**What plays the role of "vig" here is the bid/ask spread, and it is not the
same shape as sportsbook overround.** A sportsbook posts one two-sided price
pair (home American, away American) whose implied probabilities sum to
>100%; the "vig" is a property of that single joint quote. Kalshi instead
posts a **bid and an ask on each side of one binary market**, and — per §1 —
two separate per-team binary markets for one game. There are (at least) four
numbers available for a single game: `yes_bid`/`yes_ask` on team A's market
and `yes_bid`/`yes_ask` on team B's market. Because yes-team-A and yes-team-B
are complementary events (exactly one wins), consistency would require
`yes_bid(A) ≈ 1 - yes_ask(B)` and `yes_ask(A) ≈ 1 - yes_bid(B)`, but Kalshi's
order book is driven by independent traders on each side, so it is **not
guaranteed to be perfectly consistent** the way a single market-maker's
two-outcome quote is. A workable analog to `no_vig_two_way`:

- A single-market "no-vig" price analog: use the **midpoint** of
  `yes_bid`/`yes_ask` as the point estimate of implied probability for that
  team (standard practice for any bid/ask market — this is what removes the
  "vig" equivalent, i.e. the bid/ask spread, versus using the marketable side
  only). `spread = yes_ask - yes_bid` is the direct analog of `vig` in
  `NoVigMarket` — both represent the market-maker's/liquidity's take, but
  spread is a width around one number rather than an overround across a pair.
- A **cross-market consistency check** is a genuinely new concept this repo's
  two-outcome model doesn't need: comparing team A's implied probability
  (from its own market) against `1 - team B's implied probability` (from the
  complementary market) is a sanity/liquidity signal, not a normalization
  step — unlike sportsbook home/away, Kalshi's two sides aren't quoted by the
  same party, so they need not sum to 1 and probably won't exactly.

This means a full sportsbook-style `no_vig_two_way` port isn't the right
shape for Kalshi; the natural function is smaller (bid/ask midpoint per side)
plus an optional cross-check, not a booksum/overround/proportional-split
computation.

## 4. Does `src/market/engine.py` need new functions?

Given `engine.py`'s stated design ("Pure, deterministic functions that turn
American moneylines into market probabilities... No DB access lives here"),
the existing functions are specifically American-odds-shaped
(`_validate_american` rejects anything that isn't an integer with
`abs >= 100`) and cannot accept a Kalshi cents/dollars price as-is. Two
reasonable, minimal additions — not a generalized "any two-sided market"
abstraction, which the repo's own principles ("Do not add abstractions for
hypothetical future requirements") argue against building until a second
non-Kalshi yes/no source exists:

1. A small, Kalshi-specific probability helper, e.g.
   `kalshi_price_to_probability(price_dollars: float) -> float` — validates
   `0 < price < 1` (Kalshi prices are strictly between $0.01 and $0.99 while a
   market is active) and returns it as-is (it already *is* a probability;
   this function mainly exists for input validation and a single documented
   conversion point, not real "conversion" work).
2. A `kalshi_bid_ask_midpoint(yes_bid, yes_ask) -> float`-style helper (or
   inline at the call site if it's truly one line — per this repo's own
   laziness-toward-abstraction stance, this may not even warrant a named
   function separate from wherever the Kalshi snapshot is normalized).

What should **not** be built speculatively: a generalized `NoVigMarket`-like
abstraction that tries to unify American-odds two-way markets and Kalshi
single-sided-market pairs under one interface. The two are genuinely
different shapes (one joint two-outcome quote vs. two independent
single-outcome markets), and `MarketEvaluation`/`SideEvaluation`'s
`source`/`snapshot_timestamp`/provenance fields already generalize fine as
plain strings — `source="kalshi"` fits today's dataclass without a schema
change. Reuse `edge()` and `expected_value()` unchanged; both already take a
plain `model_probability` and either a market probability or an American
price, and `expected_value` would need a Kalshi-flavored sibling (EV per $1
on a $0.01–$0.99 contract is just `p_model/price - 1` for a yes contract, a
different formula from the American-odds EV) or an adapter that turns a
Kalshi price into a synthetic decimal-odds-equivalent (`decimal =
1/price_dollars`) and reuses `expected_value`'s existing decimal-odds math
via `american`-shaped input — the latter is probably not lazier than just
writing the one-line EV formula directly.

## 5. Licensing / legal / access considerations (flag, not code)

- **KYC/account requirement**: confirmed (via `kalshi.com/market-integrity/
  kyc-surveillance` and `docs.kalshi.com`) that **public market data
  (events/markets/orderbook/trades/candlesticks) requires no account, no API
  key, no KYC** — this is genuinely simpler than The Odds API in this repo's
  actual use case (read-only ingestion), which needs an API key today. KYC
  and a funded, verified account are required only if the repo ever wanted to
  *trade* on Kalshi, which is out of scope for a moneyline-comparison
  feature. **Confidence: medium-high** — this is what current Kalshi
  documentation and multiple third-party guides state consistently, but
  Kalshi's terms of service (rate limits, acceptable-use restrictions on
  redistributing or commercially using market data, any state-residency
  gating even for read access) were not independently fetched and read in
  full in this pass — **recommend reading Kalshi's actual ToS/API terms
  directly before building**, since "free and unauthenticated" doesn't
  automatically mean "no usage restrictions."
- **Regulatory/state-availability**: Kalshi restricts trading in some states;
  whether *read-only market data access* is geofenced the same way is
  **uncertain** — flagging rather than guessing.
- **Data licensing**: The Odds API is an explicit paid data aggregator with a
  commercial license for redistribution/use; Kalshi is the exchange itself
  publishing its own market's data. Terms for storing/persisting Kalshi's
  data long-term in this repo's DuckDB (as opposed to querying live) were not
  verified — flag as a prerequisite check, not a blocker assumed to be fine.

## 6. Proposed task breakdown

Following this repo's task-graph convention (`tasks/<ID>-<slug>.md`, ID
prefixes `DATA-`/`MARKET-`/`PIPE-`/`APP-`, table style matching
`tasks/index.md`). All proposed as `backlog`; goals are one line as required.
Numbering picks the next free ID in each prefix as of this research pass
(`DATA-021` is the highest existing `DATA-` id at backlog; `MARKET-002` is the
highest existing `MARKET-` id).

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-001 | Kalshi MLB market/event discovery + snapshot parser (`src/ingestion/kalshi/`), mirrors `src/ingestion/odds/snapshots.py` shape: `parse_kalshi_mlb_markets` + idempotent `ingest_kalshi_mlb_markets` into a new `bronze.kalshi_market_snapshots` table |
| DATA-023 | backlog | DATA-022, DATA-002 | Kalshi event/market -> `game_pk` identity matching (own `_match_schedule_game`-equivalent for Kalshi's per-team ticker/title text, since team abbreviations and title phrasing differ from The Odds API's full team names) |
| MARKET-003 | backlog | MARKET-001, DATA-022 | Kalshi price -> probability + bid/ask-midpoint helpers in `src/market/engine.py` (no `no_vig_two_way`-style port; see research finding that the shapes differ) |
| PIPE-006 | backlog | PIPE-004, DATA-023, MARKET-003 | extend daily pipeline's multi-book comparison artifact (`state/predictions/odds_books.jsonl` sibling, e.g. `kalshi_markets.jsonl`) to include Kalshi alongside sportsbook snapshots; display/comparison only, never the canonical `daily.jsonl` prediction input (same non-negotiable boundary PIPE-004 already draws for extra sportsbooks) |
| APP-006 | backlog | PIPE-006, APP-005 | surface Kalshi yes/no price(s) on the existing multi-book odds comparison UI (APP-005's game detail page) |

**Sequencing note:** DATA-022 and DATA-023 are the load-bearing new work —
everything else is additive display, not a change to the canonical
prediction path, matching the precedent PIPE-004/APP-005 already set for
extra sportsbook data. MARKET-003 can proceed in parallel with DATA-023 once
DATA-022's snapshot shape is fixed, since it only needs example price
payloads, not `game_pk` matching.

## Sources

- [Kalshi API docs — Get Market Orderbook](https://docs.kalshi.com/api-reference/market/get-market-orderbook) (fetched directly)
- [Kalshi API docs — Get Markets](https://docs.kalshi.com/api-reference/market/get-markets) (fetched directly)
- [Kalshi Market Integrity Hub — Mandatory KYC and Surveillance](https://kalshi.com/market-integrity/kyc-surveillance)
- [Kalshi Help Center — Kalshi API](https://help.kalshi.com/kalshi-api)
- [Kalshi Help Center — The Orderbook](https://help.kalshi.com/markets/markets-101/the-orderbook)
- [OddsShopper — Kalshi MLB Betting: Markets, Prices & Value Guide](https://www.oddsshopper.com/articles/prediction-markets/how-to-bet-mlb-on-kalshi)
- [OddsShopper — MLB Playoffs Odds 2026: Model Vs Kalshi's Board](https://www.oddsshopper.com/articles/prediction-markets/kalshi-mlb-playoffs-odds)
- [Kalshi — mlb Baseball Odds & Predictions 2026 (category page)](https://kalshi.com/category/sports/baseball/mlb/series)
- [GitHub — mmoore07129/mlb-kalshi-bot](https://github.com/mmoore07129/mlb-kalshi-bot)
- [QuantVPS — Kalshi Order Book API Explained](https://www.quantvps.com/blog/kalshi-order-book-api-endpoints-explained)
- [oddsassist — Kalshi Bid and Ask Explained](https://oddsassist.com/prediction-markets/bids-and-asks/)
- [botforkalshi.com — Kalshi API Tutorial: Auth, WebSockets, Rate Limits & Orders](https://www.botforkalshi.com/blog/kalshi-api-tutorial)
- [Kalshi live market example](https://kalshi.com/markets_by_ticker/kxmlbgame-26jul182205wshath-ath?op_market_ticker=KXMLBGAME-26JUL182205WSHATH-ATH&op_side=buy&op_order_side=no&op_order_type=dollars)
