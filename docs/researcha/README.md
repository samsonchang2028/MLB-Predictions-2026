# Research index

Pure research — no code was written or changed for any of this. Each file was
produced by an independent agent, grounded in this repo's actual code (not
generic advice), with a proposed `backlog` task breakdown at the end in
`tasks/index.md`'s table format. Nothing here is scheduled or committed to;
promoting any of it to a real task is a separate decision.

## Files

| File | Covers |
|---|---|
| [`monte-carlo-simulation.md`](./monte-carlo-simulation.md) | Simulating individual games (plate-appearance/event-based) as a possible replacement or complement to the single locked XGBoost classifier. Sized honestly as a large, mostly-new data/feature/model subsystem — the binding constraint is that Silver has no batter identity or plate-appearance-level data at all today. Proposes a new `SIM-` task prefix. |
| [`kalshi-integration.md`](./kalshi-integration.md) | Normalizing Kalshi's yes/no event-contract prices alongside The Odds API sportsbook lines. MLB coverage exists (`KXMLBGAME` series), public market data needs no auth, but Kalshi models each game as two independent single-sided markets (not one two-outcome market), so `no_vig_two_way` doesn't port directly — needs its own small helper. |
| [`weather.md`](./weather.md) | Wind/temp/precip/park-roof-state as a feature family. Recommends Open-Meteo (covers both historical backfill and live forecast from one provider). Flags the forecast-vs-actual point-in-time distinction (same shape as this repo's OPENING/SNAPSHOT/CLOSING odds vintages) and that weather doesn't fit `build.py`'s per-team home/away/diff schema — needs a game-level merge instead. |
| [`confirmed-lineups.md`](./confirmed-lineups.md) | Batting order as a feature, mirroring how starting pitchers are already tracked. Confirmed the raw data already arrives in the same `feed/live` payload PIPE-005 already re-fetches — no new fetcher needed, only a new Silver parse. Recommends a simple "regulars missing from tonight's lineup" feature over full per-batter modeling. |
| [`totals-market.md`](./totals-market.md) | Over/under runs. Ingestion needs a sibling parser to the moneyline one (totals allow multiple simultaneous lines per book, so `point` has to be part of the key). Lays out the fork between a totals-specific classifier vs. a runs-distribution/simulation approach that would also serve run lines. |
| [`run-lines-market.md`](./run-lines-market.md) | The ±1.5-run spread. Confirmed via search that MLB run lines are almost always fixed at that number, unlike NFL/NBA spreads — makes this closer to a second binary target than a variable-line problem. Flags that its ingestion needs largely overlap totals' (both need `point`-field parsing) and proposes combining them. |
| [`arbitrage-scanner.md`](./arbitrage-scanner.md) | Cross-book arbitrage detection. The math is almost entirely reuse of existing `src/market/engine.py` primitives — the real blocker isn't code, it's that odds are only fetched once/day today and real arbitrage windows close in minutes to hours. Corrected an assumption from my brief: The Odds API's pricing isn't actually the constraint (cost stays low even at 2-minute polling) — GitHub Actions cron's documented unreliability is the bigger risk if that's where it runs. **Hit a prompt-injection attempt in fetched web content** (a fake "system-reminder"); correctly ignored it, only wrote the requested file. |
| [`live-monitoring.md`](./live-monitoring.md) | Two different things bundled under one name: (A) tracking in-game odds movement (cheap — confirmed The Odds API's existing live feed already covers in-play games, no separate paid tier needed), and (B) actually predicting in-game win probability (a large, separately-sequenced effort, architecturally nothing like the current pregame model). Also caught that my brief was wrong about `OBS-002` — it's already built (result enrichment), not backlog; only its automation is missing. |
| [`ops-001-automation-and-scheduling.md`](./ops-001-automation-and-scheduling.md) | Synthesis: how to actually run the daily pipeline on a schedule, and how each feature above changes that requirement. Confirmed `data/mlb.duckdb` is ~1.1 GB, which is both why it can't just be committed to the repo (GitHub blocks any file over 100 MB outright) and why an ephemeral CI runner needs a persistence story. Found GitHub Actions' scheduled cron has documented multi-hour delay/reliability issues in 2026 — recommends a self-hosted/rented-VM cron as the default shape, cloud-storage-backed GitHub Actions as the fallback. Flags arbitrage + live monitoring as needing a *different*, minute-scale automation shape than everything else, proposed as a separate `OPS-003`. |

## Known collision: proposed task IDs overlap across files

Each agent worked independently and proposed task IDs without seeing what the
others picked, so there's real overlap — most notably **`DATA-022`**, claimed
by six different files (Kalshi, weather, confirmed lineups, totals, run
lines, live monitoring) for six different things. `MARKET-003`, `FEAT-007`,
`FEAT-008`, `APP-006`, `ML-012`, and `PIPE-006` collide similarly across a
few files each. One agent (arbitrage) also initially reached for `MARKET-002`
before catching that it's already a real, different task in `tasks/index.md`
(persisted market-relative ROI artifact) and self-corrected.

Deliberately **not** renumbering these into a single clean sequence here —
final ID assignment is a sequencing decision that belongs to whichever of
these actually gets promoted to a real task, not something to pre-decide
while nothing is being built. Treat the IDs in each file as illustrative,
not reserved.

## A few things worth noticing across files, not obvious from any single one

- **Shared ingestion surface**: totals and run lines both need the same
  `point`-field parsing on top of the existing moneyline-only odds parser —
  candidates for one combined ingestion task rather than two.
- **The Monte Carlo simulation, done fully, would subsume totals, run lines,
  and moneyline** into one internally-consistent model instead of three
  independently-trained classifiers that can produce inconsistent implied
  probabilities. Not a prerequisite for shipping the others individually,
  but worth knowing before investing heavily in a totals- or run-line-
  specific classifier.
- **Arbitrage and live monitoring are the two outliers on scheduling** — every
  other feature here fits inside the existing once-(to-a-few)-times-daily
  cadence with no new automation infrastructure. Those two need genuinely
  higher-frequency, differently-shaped automation (`OPS-003` in the
  synthesis doc), which is a materially bigger ask than the rest.
