# Research: cross-bookmaker arbitrage scanner

Status: research only, no code changes. Written to inform a future task-graph
proposal (candidate `MARKET-00X` / `OPS-00X` entries below), not to spec an
implementation.

## 1. The math (confirms this is a thin layer on existing primitives)

For a two-outcome moneyline market, an arbitrage exists when you can lock in
a bet on **both** sides — potentially at **different books** — such that you
profit regardless of which side wins. The condition, using the *best*
available decimal price for each side:

```
1 / decimal_odds(best_home_price) + 1 / decimal_odds(best_away_price) < 1
```

Each term is exactly `american_to_implied_probability` from
`src/market/engine.py` (which is `1 / american_to_decimal(american)`, line
100-105). So the arbitrage test is:

```python
implied_sum = (
    american_to_implied_probability(best_home_american)
    + american_to_implied_probability(best_away_american)
)
is_arbitrage = implied_sum < 1.0
```

This is the mirror image of `no_vig_two_way`'s `booksum` (engine.py line
150): `no_vig_two_way` computes `booksum = raw_home + raw_away` for a
**single book's own two-sided line** and treats `booksum > 1` (positive vig)
as the normal case. An arbitrage is the cross-book analog: take the best
home price from book A and the best away price from book B and the
combined "booksum" comes out `< 1` — the two books disagree enough that
their combined implied probabilities don't even cover 100%, which is exactly
what a single book's own vig is supposed to prevent. `no_vig_two_way` itself
cannot be reused directly for the arbitrage check (it's built for one book's
matched pair and raises if the sum is degenerate in the *high* direction,
never asserts on the low side) — but `american_to_decimal` and
`american_to_implied_probability` are exactly the two calls needed, verbatim,
with no new odds-math primitives.

**Profit margin.** The standard "arbitrage percentage" is `implied_sum`
itself; guaranteed return on total stake is:

```
profit_margin = 1 / implied_sum - 1
```

e.g. `implied_sum = 0.97` -> `profit_margin ≈ 3.09%` guaranteed regardless of
outcome (before considering execution risk — see caveats below).

**Stake sizing.** To lock in equal profit on either outcome, stake
proportionally to each side's implied probability share of the total bankroll
`S`:

```
stake_home = S * implied_home / implied_sum
stake_away = S * implied_away / implied_sum
```

Both legs then return `S / implied_sum` if they hit, i.e. profit
`S * (1/implied_sum - 1)` no matter which side wins. This stake-sizing
formula is new arithmetic (a couple of lines), not present anywhere in
`engine.py` today, but it composes from values `engine.py` already produces
(`american_to_decimal` / `american_to_implied_probability`) — nothing here
needs a new odds-conversion formula.

**Practical caveat worth stating up front (not just theoretical):** this
"guaranteed" framing assumes both books honor the posted price at bet time.
In practice, moneyline arbitrage carries real execution risk — line movement
between placing leg 1 and leg 2, bet-limit/liability caps that reject large
"sharp" bets, and account restrictions books apply to bettors who repeatedly
place arbitrage-shaped bets. None of that is representable from a passive
odds-snapshot dataset; a scanner can only flag *candidate* windows, not
guarantee executable profit.

## 2. Why once-a-day snapshots don't support this feature today

Today's pipeline (`scripts/daily_predictions.py`, `main()`) fetches odds
exactly twice **per pipeline run**, and the pipeline runs once per day
(PIPE-002/PIPE-003 design; OPS-001 — scheduled automation — is still
`backlog` per `tasks/index.md` line 66):

```
line 904: payload = fetch_odds_payload(api_key, regions=args.regions, bookmakers=args.bookmaker, ...)   # canonical, DraftKings
line 907: comparison_payload = fetch_odds_payload(api_key, regions=args.regions, bookmakers=None, ...)   # all US books, comparison-only
```

Both calls happen back-to-back in the same run, so every row in
`state/predictions/odds_books.jsonl` for a given `(run_date, game_pk,
bookmaker)` reflects one instant, once a day (PIPE-004 handoff, `tasks/PIPE-
004-prediction-detail-artifacts.md`: `odds_books.jsonl` "tracks the *current*
price per book, not a tick-by-tick history").

Real moneyline lines move continuously from the morning through first pitch
as books react to sharp money, injury/starter news, and each other — that's
precisely the mechanism that closes arbitrage windows. Genuine cross-book
mispricings (the `implied_sum < 1.0` condition above) are typically
short-lived: minutes to a few hours, often closing the moment one book
copies another's correction. A single daily snapshot can occasionally land
inside such a window by luck, but it cannot *detect* one systematically — it
has no way to distinguish "there was a real arb window today" from "we
happened not to be looking when one existed." Any scanner built purely on
today's cadence would produce a feed that's silent almost all the time and,
when it does fire, is very likely already stale (the opportunity gone by the
time a human reads the Streamlit page).

**What would actually be needed:** odds polling on the order of every few
minutes (not once daily) throughout each day's pregame window, so successive
`odds_books.jsonl` snapshots are close enough in time to catch a mispricing
before it closes. This is explicitly **not** a code-logic change — the
ingestion code (`fetch_odds_payload`, `all_book_snapshots_for_schedule`) and
the `append_jsonl_records(..., on_conflict="overwrite")` upsert writer
already support being called repeatedly through a day; nothing about them
assumes once-daily invocation. It is a **scheduling/infrastructure**
question squarely inside `OPS-001`'s existing scope (`tasks/OPS-001-daily-
operator-automation.md`, requirement 6: "Automation should run multiple
daily prediction refreshes when useful because probable starters and odds
move throughout the day" — already anticipates more-than-once-daily runs,
just not yet at arbitrage-relevant frequency). Any arbitrage feature is
therefore blocked less by new code and more by however `OPS-001` resolves
its own open question of run frequency and hosting (GitHub Actions vs. local
operator) — see the task table below.

## 3. Is frequent polling realistic on The Odds API's actual limits/pricing?

Confirmed via the vendor's own docs and pricing page (the-odds-api.com,
current as of this research):

- **Quota model**: usage is metered in **credits**, not raw request counts.
  `GET /odds` costs `[markets requested] x [regions requested]` credits per
  call — 1 credit per market-region pair. The `bookmakers` param (used
  instead of `regions`) converts to an equivalent region cost (up to 10
  bookmakers ≈ 1 region's cost, 11-20 ≈ 2 regions). This repo's calls use
  `regions=us` (not `bookmakers=`) for the multi-book comparison fetch
  (`daily_predictions.py` line 907, `bookmakers=None`), so each call is
  **1 credit** (1 market `h2h` x 1 region `us`) regardless of how many US
  books come back in the response.
- **Published tiers**: Free/"Starter" — 500 credits/month, all sports and
  markets. Paid: **20K** plan $30/mo for 20,000 credits/month; 100K plan
  $59/mo for 100,000 credits/month; higher tiers ($119/mo for 5M,
  $249/mo for 15M) exist but are far beyond what a single MLB slate needs.
- **Rate limit**: 30 requests/second on paid plans — irrelevant at
  minutes-level polling; never the binding constraint here.

**Concrete cost estimate for arbitrage-relevant polling**, assuming
polling continues only during each day's pregame window (roughly a 10-12
hour span covering first pitches from ~10am PT day games through ~7pm PT
evening starts) and one comparison-only call per poll (1 credit each, per
the math above):

| Poll interval | Polls/day (12h window) | Credits/day | Credits/month (~30 days) | Fits which tier |
|---|---|---|---|---|
| every 15 min | 48 | 48 | ~1,440 | comfortably inside 20K plan ($30/mo); tight but survivable on free 500/mo only if games run <11 days/month, i.e. not viable on free tier for a full season |
| every 5 min | 144 | 144 | ~4,320 | comfortably inside 20K plan ($30/mo) |
| every 2 min | 360 | 360 | ~10,800 | still inside 20K plan ($30/mo), ~54% of quota |
| every 1 min | 720 | 720 | ~21,600 | exceeds 20K plan; needs 100K plan ($59/mo) |

**Bottom line**: the vendor's pricing is not the blocker for a
minutes-level poll of a single sport/region — even 2-minute polling stays
within the cheapest paid tier ($30/mo), and this repo already pays nothing
today (once-daily, 2 credits/day, trivially inside the free 500/month tier).
The realistic constraints are (a) this project has been operating on the
free tier so far, so *any* frequent-polling design forces a real "are we
willing to pay $30+/mo" decision that doesn't exist today, and (b) the
actual scheduling infrastructure to *trigger* a job every 2-5 minutes
reliably. If `OPS-001` lands on GitHub Actions (the option it names first),
GitHub's own `schedule` trigger has a documented minimum granularity of 5
minutes and — more importantly — **no guaranteed execution latency**: on
the free/shared runner queue, scheduled workflows are explicitly
best-effort and commonly delayed well beyond their nominal interval during
platform load, which directly undermines the "catch a window that closes in
minutes" goal this feature exists for. A local/always-on operator (cron,
systemd timer, or a small always-on process) would hit the requested
interval far more reliably than GitHub Actions cron, but that's a hosting
decision, not something this research should presume — it's exactly the
open question `OPS-001` is scoped to resolve, and this feature inherits
whatever `OPS-001` decides.

## 4. Where the scanning logic would live

This is close to pure reuse: `odds_books.jsonl` (PIPE-004) already has, per
`(run_date, game_pk, bookmaker)`, exactly the `home_american` /
`away_american` pair needed. `src/app/game_detail.py`'s
`_best_price_for_side` (lines 195-204) already implements half of the
pattern — "best (highest-payout) price for one side across books" — using
`max(candidates, key=lambda book: american_to_decimal(book[field]))`. The
new logic is the same best-price-per-side selection, done for **both**
sides in the same game, plus the `< 1.0` comparison:

Proposed location: a new `find_arbitrage(odds_books: list[dict]) -> Arbitrage
| None` function, most naturally in `src/market/engine.py` alongside
`no_vig_two_way`/`edge` (it's the same "market probability" family of
functions and needs `american_to_decimal`/`american_to_implied_probability`
directly), or split out to a new sibling module `src/market/arbitrage.py` if
the repo's maintainers prefer keeping `engine.py` to single-book math and
isolating the cross-book concern — either is a reasonable, small choice; the
task file should pick one rather than presupposing it. Rough shape (research
sketch only, not proposed code to merge as-is):

```python
def find_arbitrage(odds_books: list[dict]) -> ArbitrageOpportunity | None:
    """Best home/away price across books for one game; flag if implied_sum < 1.0."""
    homes = [b for b in odds_books if b.get("home_american") is not None]
    aways = [b for b in odds_books if b.get("away_american") is not None]
    if not homes or not aways:
        return None
    best_home = max(homes, key=lambda b: american_to_decimal(b["home_american"]))
    best_away = max(aways, key=lambda b: american_to_decimal(b["away_american"]))
    implied_sum = (
        american_to_implied_probability(best_home["home_american"])
        + american_to_implied_probability(best_away["away_american"])
    )
    if implied_sum >= 1.0:
        return None
    return ArbitrageOpportunity(
        home_book=best_home["bookmaker"], home_american=best_home["home_american"],
        away_book=best_away["bookmaker"], away_american=best_away["away_american"],
        implied_sum=implied_sum, profit_margin=1.0 / implied_sum - 1.0,
    )
```

Note this deliberately does **not** require `best_home` and `best_away` to
come from different books — if the same book happens to offer the best price
on both sides, `implied_sum` for a single sane book is virtually always
`>= 1.0` (that's the vig), so the degenerate same-book case self-excludes
without needing an explicit check; still worth a unit test asserting that
directly rather than assuming it. The whole function is <20 lines and adds
zero new odds-conversion math — it is `_best_price_for_side` applied twice
plus one inequality, exactly as the task context anticipates.

## 5. Display and alerting

**Passive display (cheap, works today):** the existing Streamlit Game Detail
page (`src/app/game_detail.py` / `load_game_detail`) already loads
`odds_books` per game and renders a per-book table. Adding a "best home
price" / "best away price" / arbitrage-margin row to that same page is a
small, additive change — call `find_arbitrage(odds_books)` once
`odds_books` is loaded (line 103 in the current file) and add the result to
the returned dict, then render it conditionally in the page. This works with
**zero cadence change** and is honest about what it is: a check of whatever
snapshot happened to be captured today, not a live monitor. It should be
labeled as such in the UI (e.g. "as of last snapshot, HH:MM Pacific") so
users don't mistake it for a live feed.

**Active alerting ("tell me when one appears"):** this is a genuinely
different feature — it requires a process running continuously (or on a
tight schedule) that re-evaluates `find_arbitrage` after every fresh poll
and pushes a notification (email/Slack/webhook/etc.) the moment
`implied_sum < 1.0` appears, because a human periodically checking a
Streamlit page cannot react inside the minutes-scale window before it
closes. Building this on top of once-daily polling would be close to
theater: the check would almost always run on stale, already-resolved odds,
producing either silence or an alert for an opportunity that's already gone
by the time it fires. Alerting is only worth building once section 2/3's
higher-frequency polling exists; until then, the honest scope is
"passive display, refreshed whenever the pipeline happens to run."

## 6. Proposed task breakdown

Matches `tasks/index.md`'s table style; both rows would start at `backlog`
per repo convention for unstarted work, consistent with `OPS-001`'s current
status.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| MARKET-002 | backlog | MARKET-001, PIPE-004 | cross-book arbitrage detection (`find_arbitrage`) reusing `american_to_decimal`/`american_to_implied_probability` against existing `odds_books.jsonl`; passive Game Detail display only, no alerting; small/cheap, no new odds-math primitives |
| OPS-003 | backlog | OPS-001 | high-frequency (minutes-scale) odds-only polling infrastructure during each day's pregame window, independent of the once-daily prediction run; decides hosting (GitHub Actions cron vs. local/always-on operator) and confirms real execution latency meets the interval; prerequisite for MARKET-002 to be more than a once-daily curiosity |
| MARKET-003 | backlog | MARKET-002, OPS-003 | active arbitrage alerting (push notification the moment a scan finds `implied_sum < 1.0`); not worth building before OPS-003 lands since alerting on once-daily data is close to useless |

Note: `tasks/index.md` already lists `MARKET-002` as a **different**,
already-planned task ("persisted market-relative report/ROI artifact",
depends on `MARKET-001, OBS-002`, status `backlog`). The arbitrage-detection
task above would need a different free ID (e.g. `MARKET-003` for detection
and `MARKET-004` for alerting, or renumber depending on which lands first) —
flagging this collision explicitly rather than silently claiming a
already-spoken-for ID.

## Summary

- The core arbitrage math is ~15-20 lines reusing `american_to_decimal` /
  `american_to_implied_probability` verbatim, plus one new stake-sizing
  formula — genuinely small, genuinely mostly-reuse, as anticipated.
- The real cost/complexity is not in that function; it's in how often odds
  get fetched. Once-daily (today's cadence) makes any arbitrage check
  observational trivia, not a usable signal — arbitrage windows close on a
  minutes-to-hours timescale.
- The Odds API's pricing/quota is *not* the blocker: even 2-minute polling
  during pregame windows fits inside the cheapest paid tier ($30/mo, 20K
  credits), because this repo's calls are metered per market x region (1
  credit), not per bookmaker. The real open questions are (a) willingness to
  move off the free tier at all, and (b) whether the chosen scheduler
  (GitHub Actions cron's documented 5-minute-minimum-and-best-effort-latency
  behavior, specifically) can actually hit a tight enough interval — both of
  which are `OPS-001`'s open scope, not new questions this feature invents.
- Recommend shipping the passive-display detector (`MARKET-002`/`003` above)
  independently of and before any polling-frequency change — it's cheap,
  correct, and honestly labeled as "checked against the last snapshot," and
  it gives a concrete example to point at when `OPS-001`/`OPS-003` scope the
  real infrastructure question.
