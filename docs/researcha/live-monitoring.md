# Research: Live Monitoring

Status: research only. No code changed as part of this document.

"Live monitoring" is ambiguous and covers at least two distinct pieces of
work, plus a third piece that's already half-scoped in this repo under a
different name. This doc separates them so a reader can pick a scope instead
of getting one lumped answer.

- **A. In-game odds/outcome tracking** — ingest what happens to a game (line
  movement and/or final result) once it starts.
- **B. Live-updating dashboard/alerting** — make the Streamlit app refresh
  itself instead of requiring a manual reload, and optionally notify on
  events.
- **A widening into "predict live win probability"** is a separate, much
  larger thing than either of the above and is called out explicitly so it
  doesn't get scoped as a cheap add-on to A.

Everything here is additive to the existing pregame model. Nothing proposed
changes `ADR-002` (`docs/decisions/ADR-002-point-in-time.md`) or the
`snapshot_timestamp < prediction_timestamp < game_start_timestamp` guard
enforced in `src/market/engine.py` (`snapshot_is_pregame_valid`,
`evaluate_pregame`) and `src/pipelines/daily.py`
(`SKIP_NOT_BEFORE_FIRST_PITCH`, `SKIP_ODDS_NOT_BEFORE_CUTOFF`). The existing
pregame prediction is, and stays, a single immutable pre-first-pitch record.
Anything "live" is a new, separate, clearly-labeled data stream sitting next
to it — never a replacement input to `run_daily_predictions`.

---

## Interpretation A — in-game odds tracking (observational only)

### What it is

Once a game starts, sportsbook lines keep moving (they usually collapse
toward heavy favorite/underdog as the game state resolves, e.g. moneyline on
the leading team drifts toward -2000+ late). Capturing that movement is
useful context — "did the market already know something the model didn't
catch pregame" — but it is explicitly **not** a new prediction. This repo's
one locked model (`ML-009`/`ADR-006`) was trained on pregame features only;
feeding it in-game odds would be nonsensical (nothing in the feature set
represents score/inning/base-out state), and generating a *new* probability
from those odds directly (e.g. treating in-game no-vig market probability as
"the model's live prediction") would misrepresent market odds as a model
output.

### What it takes (small, mostly ingestion)

1. A new `MarketLabel` state, e.g. `LIVE`, alongside `OPENING` / `SNAPSHOT` /
   `CLOSING` in `src/market/engine.py`. It must **never** satisfy
   `evaluate_pregame`'s guard — the cleanest way is to reject `LIVE` there
   exactly like `OPENING` is rejected today (`label is MarketLabel.OPENING`
   → `ValueError`), and route it only through `evaluate_benchmark`-style
   post-hoc/observational paths, never `evaluate_pregame`.
2. Ingestion: The Odds API's existing `/odds/` endpoint already returns
   in-play games in the same feed as upcoming games (confirmed — see
   Sources; this is not a separate paid product tier, contrary to the
   "separate tier" framing in the task prompt: the standard live odds feed
   itself already includes in-play games, refreshed as often as ~30s,
   billed under the same per-request credit model this repo already uses
   for `DATA-003`/`PIPE-003` pregame odds calls). So there's no new vendor
   integration needed — just calling the same endpoint on a schedule that
   extends past first pitch, and storing what comes back with a `LIVE`
   label instead of discarding/ignoring it.
3. Storage: reuse the append-only, immutable, timestamped odds-observation
   pattern already established by `DATA-003` (source, book, home/away
   outcome, American price, snapshot timestamp, commence time). In-game
   snapshots are just more rows with `LIVE` label and `snapshot_timestamp >
   game_start_timestamp`.
4. Display: a "line movement" chart/table on the existing game detail page
   (`src/app/game_detail_page.py`, which already reads multi-book odds
   comparison artifacts from `PIPE-004`) is a natural, additive extension —
   it already has the per-game detail surface; it would just show more rows
   over time, explicitly labeled as post-game-start/observational.

### Sizing

Cheap. It's the same ingestion shape as existing `DATA-003`/`PIPE-003` odds
polling, pointed at a longer time window and stamped with a new label that
is structurally prevented from entering `evaluate_pregame`. No model
retraining, no new features required for this piece alone.

---

## Interpretation A-prime — actually predicting in-game win probability

This is the one piece worth being very honest about: **do not confuse "track
live line movement" with "produce a live win-probability number."** They
sound adjacent; they are not the same size of project.

### How public in-game MLB win-probability models actually work

Confirmed via research (FanGraphs' Win Expectancy / Sabermetrics Library —
see Sources): these are not a simple regression on the existing pregame
feature set with a couple of extra columns. They are built on a *base-out
state* representation:

- 3 out-states (0/1/2) x 8 baserunner configurations = 24 base-out states,
  combined with inning, top/bottom half, and score differential — commonly
  written as a `BBB_O_HI_RD` state string (baserunners, outs, half-inning,
  inning, run differential).
- Win expectancy for a given state is essentially an empirical lookup table
  (or smoothed regression over one) built from historical frequency of
  teams in that exact state going on to win, in a given run-scoring
  environment.
- This requires an entirely different data spine: live play-by-play or
  at-least half-inning-resolution game state (score, inning, half, outs,
  baserunners) — none of which exists anywhere in this repo today. Today's
  ingestion (`src/ingestion/`, `DATA-002`/`DATA-005`) is pregame
  schedule/probable-starter/pitcher data, not live play-by-play.

### Why this is a separate project, not an extension

- New data source: live play-by-play/game-state feed (MLB Stats API's live
  game feed is the typical public source; this repo has no ingestion for it
  today).
- New feature representation: base-out-state x inning x score differential,
  nothing like the current starter/bullpen/team feature matrix
  (`src/features/build.py`).
- New model: a live win-probability model is normally a lookup table or a
  small regression over historical state frequencies — conceptually
  simple, but it is a *different model with a different training set*, not
  a fine-tune of the locked pregame classifier. It would need its own
  methodology decision (an ADR), its own train/validation discipline
  (`ML-004`-style walk-forward, its own calibration comparison), separate
  from the frozen `ADR-006` pregame methodology.
- Evaluation surface: log loss/Brier/calibration per game-state bucket,
  which is a new evaluation harness, not a reuse of `ML-007`/`ML-008`.

None of this is required to get value out of interpretation A (line
tracking) or interpretation B (dashboard refresh). It's called out so a
future task doesn't accidentally get scoped as "add a LIVE odds label" when
what's actually wanted is "predict win probability mid-game," which is a
multi-task `ML-01x` effort on par with the original `ML-001`..`ML-010` chain,
not a follow-on to `MARKET-001`.

---

## Interpretation B — live-updating dashboard / alerting

### Current state

`src/app/daily_board_page.py` and `src/app/game_detail_page.py` are plain
Streamlit scripts: they read `state/predictions/daily.jsonl` (and
`journal.jsonl`, feature/odds-book artifacts) fresh on every script
execution, which Streamlit re-runs on user interaction or a manual browser
reload. There is no auto-refresh timer and no push mechanism (no
websocket/SSE) anywhere in `src/app/`.

### What "live-feeling" would take

Two tiers, in increasing cost:

1. **Polling auto-refresh (cheap, native to Streamlit).** Wrap the parts of
   the page that should update in `st.fragment(run_every="30s")` (or
   `st.rerun()` inside a timed loop) so the board/detail page silently
   re-reads the same JSONL/artifact files on an interval without a manual
   reload. This is a UI-only change — it re-reads exactly the files already
   being read, just on a timer instead of on click. No new data pipeline.
2. **Genuinely push-based (expensive, not warranted here).** A
   websocket/SSE layer pushing new predictions/results to the browser the
   instant they're written server-side. Given this repo's actual write
   cadence (a handful of daily pipeline runs, not a stream), this is
   solving a problem the data doesn't have yet — flagged explicitly as
   over-scoped for now.

### The real constraint: refresh is pointless faster than the data changes

Auto-refreshing the UI every 30 seconds is free in Streamlit, but it's
theater if `state/predictions/daily.jsonl` and `journal.jsonl` themselves
only change once (or a few times) a day. **This loops directly back to
`OPS-001`** (`tasks/OPS-001-daily-operator-automation.md`, currently
`backlog`), whose own requirement #6 already says "Automation should run
multiple daily prediction refreshes when useful because probable starters
and odds move throughout the day." A UI auto-refresh task is only worth
doing *after or alongside* `OPS-001`, otherwise it's polling a file that
hasn't changed.

### What "alerting" would concretely mean

- **Edge-threshold alert**: notify (desktop notification / log line / email
  / Slack — no infra for any of these exists yet, so pick the cheapest:
  probably a log line or a Streamlit `st.toast`/banner first) when a
  newly-written prediction record's `|edge|` crosses the same
  `DEFAULT_EDGE_THRESHOLD` already displayed on the daily board
  (`src/app/board.py`, referenced in `daily_board_page.py`'s caption). This
  reuses an existing threshold constant — no new business logic, just a
  notification hook on write.
- **Arbitrage-opportunity alert**: cross-reference whatever the parallel
  arbitrage-scanner research produces. If that work defines an "arbitrage
  detected" event/record, alerting on it is the same shape as the
  edge-threshold alert (notify when a new record of a given kind appears)
  — this doc does not re-derive arbitrage scope, just notes the alert
  mechanism would be shared.
- Both of the above are consumers of records that already get written by
  existing/planned operators (`PIPE-002`/`OPS-001` for predictions,
  whatever the arb-scanner task turns out to be) — the "alerting" piece
  itself is small (watch a file/store for new rows matching a condition,
  emit a notification), and it inherits the same OPS-001 cadence
  dependency as the dashboard refresh above.

---

## The genuinely useful first step, without overbuilding

Given the current once-daily (or manual) cadence, the smallest real slice of
"live monitoring" that's actually useful today is **not** live odds and
**not** a live model. It's tracking the *actual result* of a game so a
prediction's correctness is knowable — and that's `OBS-002`.

Confirmed by reading `tasks/index.md` and
`tasks/OBS-002-result-enrichment-operator.md`: `OBS-002` is already
`candidate (implemented; awaiting review/tester)` — it adds
`scripts/enrich_prediction_results.py`, which refreshes the MLB schedule
after games finish and appends immutable result-enrichment rows
(`home_score`, `away_score`, correctness) to
`state/predictions/journal.jsonl`, already joined into the daily board's
display. In other words: **the "did this game finish, and was the pick
right" piece of live monitoring is already done, not backlog** — this
research should not propose a duplicate task for it. The only relevant
follow-on is making it run automatically instead of manually, which is
exactly `OPS-001`'s job (`OPS-001` already lists `OBS-002` as a dependency).

So the actual ordering that avoids overbuilding is:

1. `OPS-001` (backlog) — automate the runs that already exist (multiple
   daily prediction refreshes + post-game `OBS-002` enrichment) on a
   schedule. This alone makes the "board looks live" problem mostly
   disappear without touching the UI at all, since the underlying files
   start changing multiple times a day on their own.
2. Only then does a UI auto-refresh (`st.fragment`) start being worth
   building — polling data that's actually moving.
3. In-game odds tracking (`LIVE` label + ingestion) is an independent,
   cheap, parallel-safe addition any time — it doesn't block or get blocked
   by 1/2.
4. In-game win-probability modeling is its own multi-task effort, sequenced
   whenever there's appetite for a second model line; not a dependency of
   anything above.

---

## Proposed task breakdown

Matches `tasks/index.md` conventions. All `Status: backlog`. IDs are
proposals, not reservations — next available number in each prefix as of
this writing (checked against `tasks/index.md`: current backlog/candidate
max is `DATA-021`, `OBS-002`, `APP-005`, `PIPE-005`, `MARKET-002`).

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-003 | ingest in-play odds via existing The Odds API live feed; new `LIVE` `MarketLabel`, structurally rejected by `evaluate_pregame`; append-only, observational only |
| APP-006 | backlog | DATA-022, APP-005 | game detail page: show in-game line-movement history alongside existing multi-book pregame odds comparison; explicitly labeled post-first-pitch/observational |
| APP-007 | backlog | OPS-001 | Streamlit auto-refresh (`st.fragment`/timer) for daily board + game detail; sequenced after OPS-001 so it polls data that's actually changing intraday, not a once-a-day file |
| OBS-003 | backlog | OBS-002, OPS-001 (for cadence), (arb-scanner task, if adopted) | alerting hook: notify when a new prediction's `|edge|` crosses the existing board threshold, or when a new arbitrage-opportunity record appears; reuses `DEFAULT_EDGE_THRESHOLD` and existing journal/store writes as the event source |
| ML-012 | backlog | ML-010, (new live play-by-play ingestion, not yet scoped) | in-game win-probability model: base-out-state x inning x score-differential lookup/regression, new data spine (live play-by-play), new ADR, new evaluation harness; explicitly NOT an extension of the locked ADR-006 pregame model — separate methodology decision required before implementation starts |

Notes on dependencies:

- `APP-007` and `OBS-003` both gate on `OPS-001` for the same reason: a
  live-feeling UI or a timely alert is only as good as how often the
  underlying data changes, and today that's "once a day, manually."
- `DATA-022` has no dependency on `OPS-001` — it's a straight ingestion
  task and can run in parallel with everything else in this table.
- `ML-012` is deliberately not sequenced against the others; it doesn't
  share files or data with them and shouldn't block/be blocked by the
  smaller items. It's sized closer to the original `ML-001..ML-010` chain
  than to a single task.

---

## Sources

- [MLB Odds API | The Odds API](https://the-odds-api.com/sports-odds-data/mlb-odds.html)
- [API Reference & Documentation - The Odds API](https://theoddsapi.com/docs/)
- [Sports Odds API FAQ: Pricing, Coverage, Edge Detection | The Odds API](https://theoddsapi.com/faq)
- [Win Expectancy | Sabermetrics Library (FanGraphs)](https://library.fangraphs.com/misc/we/)
- [WPA | Sabermetrics Library (FanGraphs)](https://library.fangraphs.com/misc/wpa/)
- [How Win-Probability Models Work (And Why They Disagree) — MLBAnalytic](https://mlbanalytic.com/articles/how-win-probability-models-work.html)
