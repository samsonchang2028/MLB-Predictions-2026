# Research: Monte Carlo Game Simulation for MLB Predictions

Status: research only, nothing implemented. No code, tasks, or existing files
were changed to produce this document.

Scope per request: evaluate Monte Carlo (event-based, plate-appearance-level)
game simulation as (a) an alternative/companion to the locked single XGBoost
win-probability classifier (ADR-006), and (b) a way to derive secondary
markets (run totals, run lines, player props) from one simulation instead of
training a separate model per market.

---

## 1. What Monte Carlo game simulation means for baseball

Baseball is unusually well suited to event-based simulation because a half
inning is a finite-state Markov chain: 24 base/out states (0-3 runners on
first/second/third × 0-2 outs) plus one absorbing "3 outs" state. A plate
appearance is a transition from one state to another (or to a run-scoring
event that also changes the state). This is the standard academic and
sabermetric framing — see Bukiet, Harold, and Palacios's original Markov
chain treatment and subsequent teaching material built directly on it
([A Markov Chain Approach to Baseball](https://www.researchgate.net/publication/238836772_A_Markov_Chain_Approach_to_Baseball),
[An Intuitive Markov Chain Lesson From Baseball](https://pubsonline.informs.org/doi/pdf/10.1287/ited.5.1.47)).

Two ways to use that chain, and public projects use both:

- **Analytic Markov solution**: build a 24×24 (or 25×25 with the absorbing
  state) transition-probability matrix from PA outcome probabilities, then
  solve for expected runs per inning / run-scoring distribution by matrix
  power or linear algebra, no random sampling needed. Fast, gives expected
  values and full theoretical distributions, but is awkward once you want
  game-level stochastic variance (bullpen usage, lead-change dynamics,
  pinch-hitting) rather than a single inning in isolation.
- **Monte Carlo / trial-based simulation**: literally draw a random outcome
  for each plate appearance from that team's batter-vs-pitcher outcome
  distribution, advance runners, repeat for 9 (or more) innings, thousands to
  millions of times, and read off the empirical distribution of runs scored,
  the winner, margin, etc. This is what "Markov Chain Monte Carlo" (MCMC) in
  the baseball context usually refers to in practice — combining the same
  state machine with repeated random draws instead of solving it in closed
  form ([Markov Chain Baseball Models](https://medium.com/analytics-vidhya/markov-chain-baseball-models-31bd52c422d3)).

**Where the PA outcome probabilities come from** (the part this repo does not
have yet, see §2): every serious implementation needs a per-plate-appearance
outcome distribution (walk / HBP / strikeout / groundout / flyout / single /
double / triple / home run, etc.) for a *specific batter facing a specific
pitcher in a specific park*, not a league-average rate. The standard technique
is the **log5 / odds-ratio method**: combine the batter's rate, the pitcher's
rate, and the league rate in odds space for each outcome, which is only
strictly valid for a binary (two-outcome) split, so real implementations
compute it hierarchically (e.g. batter-out-rate vs pitcher-out-rate first,
then split hit types) or replace it with a small multinomial/Bayesian model
over outcome categories ([odds ratio method / Tom Tango](https://technology.mlblogs.com/baseball-public-dataset-51c3aeb89322),
[Bayesian batter/pitcher matchup modeling, PLOS One](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0204874)).
Park factors then reweight the home-run/extra-base-hit components per venue.
Publicly documented large-scale projects (FanGraphs ZiPS/Steamer "Depth
Charts", used for their playoff-odds pages) work the same way at the season
level: build a percentile player-level rate projection, assemble it into a
lineup/roster, and run on the order of **a million Monte Carlo trials** per
question ("[ZiPS then generates a million versions of each team in Monte
Carlo fashion](https://blogs.fangraphs.com/the-late-january-zips-projected-standings-update/)").
FiveThirtyEight's retired MLB model took a lighter-weight route for single
games: an Elo-derived win probability converted to a runs-scored distribution
via a fitted scoring model, then simulated at the game level rather than the
plate-appearance level ([How Our MLB Predictions Work](https://fivethirtyeight.com/methodology/how-our-mlb-predictions-work/)).
That second, coarser approach is worth naming explicitly in §4 because it is
a much smaller lift than full PA-level simulation and this repo could
plausibly do it with data it already has.

The canonical training data for the fine-grained approach is **Retrosheet
event files** (or Statcast/pitch-by-pitch data), which carry one row per
plate appearance with the batter, pitcher, base/out state, and outcome —
exactly the granularity a PA-level simulator consumes
([Retrosheet Public Dataset](https://technology.mlblogs.com/baseball-public-dataset-51c3aeb89322)).

---

## 2. Data gap: what this repo has vs. what simulation needs

Grounded in the actual Silver contract (`src/transforms/silver.py`) and the
feature builders that consume it (`src/features/team.py`, `src/features/starter.py`,
`src/features/bullpen.py`):

| Table | Granularity | Usable for simulation? |
|---|---|---|
| `silver.games` | one row per game | Yes — schedule/park/date scaffolding only |
| `silver.team_game_statistics` | one row per `(game_pk, team_id)`: `score`, `is_winner`, `league_wins/losses/pct` | **No batting boxscore stats at all.** No hits, at-bats, walks, or any offensive rate inputs — only final score and record. `src/features/team.py`'s own docstring says this outright: *"OPS/OBP/SLG, K%, BB%, and similar offense metrics are unavailable in the current Silver contract; V1 offense proxies are runs scored / allowed only."* |
| `silver.pitcher_appearances` | one row per `(game_pk, team_id, appearance_order)`: IP, BF, pitches, strikes/balls, H/R/ER/BB/K/HR allowed | **Per-pitcher, per-game aggregate line only.** No per-batter-faced outcome sequence, no base/out state, no opposing batter identity, no pitch type/location. Good enough for rolling *rate* features (ERA-ish, K/BF, BB/BF, HR/BF) but not for reconstructing or drawing individual PA outcomes against a specific batter. |
| `silver.pitcher_starters` | starter identity only | No stat content |
| `silver.odds_snapshots` / mapping | market data | Unrelated to simulation inputs directly, but relevant for calibrating derived markets (§4/§5) |

**Conclusion: this repo has no batter-level data at all** (no batter ID
appears anywhere in Silver) and no plate-appearance/pitch-level event data.
Everything currently ingested is *pitcher*-appearance-level and *team*-game
final-score-level. A PA-level Monte Carlo simulation as described in §1 would
require new ingestion the repo does not have today:

1. **New Bronze source**: MLB Stats API's `playByPlay`/`liveData.plays` feed
   (available for the same `game_pk`s already fetched for `bronze.mlb_game_detail_payloads`
   in `src/ingestion/mlb/game_detail.py`) or a Statcast/Retrosheet-style event
   feed, giving one row per plate appearance: batter ID, pitcher ID, base/out
   state pre-PA, outcome, resulting state, and (ideally) park.
2. **New Silver table**, e.g. `silver.plate_appearances`, keyed by
   `(game_pk, plate_appearance_index)` or `(game_pk, at_bat_index)`, carrying
   `batter_id`, `pitcher_id`, `inning`, `half`, pre-PA base/out state, and a
   normalized outcome category. This is a genuinely new normalization
   surface, not an extension of an existing table — it has no natural home in
   `team_game_statistics` or `pitcher_appearances`.
3. **New batter identity/rate history** entirely — today there is no batter
   table or batter-keyed rolling feature anywhere in the codebase (confirmed:
   no `batter_id` field exists in any Silver table or feature builder). A
   simulation needs season/rolling batter rate stats the same way
   `src/features/starter.py` already builds them for pitchers.
4. **Park factors** — not currently ingested or referenced anywhere in
   `src/features/` or `src/market/`. Would need a new small static/versioned
   dataset (park run-scoring factors by year), since the MLB Stats API game
   payload already carries venue ID and this repo already retains `game_json`
   verbatim in `silver.games.source_game_json`, so venue identity is
   recoverable without new ingestion — only the park-factor *values* are new.

A coarser, game-level approach (per §1's FiveThirtyEight example — simulate
final score margins from a fitted scoring distribution conditioned on the
existing team/starter/bullpen rolling features, rather than simulating
individual plate appearances) would **not** need any new ingestion: it could
reuse exactly the Gold matrix `src/features/build.py` already assembles.
That is a materially smaller and more honest starting point for this repo
than full PA-level simulation (see §4/§5).

---

## 3. Point-in-time correctness for a simulation approach

ADR-002's rule — every feature value must be reproducible from information
available strictly before `prediction_timestamp` — applies to simulation
*inputs* exactly as it does to today's rolling features, and does not get
easier just because the inputs feed a simulator instead of a boosted tree:

- Batter and pitcher rate stats used to parameterize each simulated PA must
  be computed only from that player's completed PAs/appearances **before**
  the game being predicted — same shift-before-roll discipline as
  `src/features/starter.py` and `src/features/bullpen.py` already implement
  for pitcher rolling windows. A new `silver.plate_appearances`-derived
  batter-rate builder would need the identical "current-game line is a
  post-game outcome fact, excluded until shifted" contract that
  `src/transforms/silver.py`'s docstring already states for
  `pitcher_appearances`.
- The **starting lineup** used to seed the 9 (or fewer, with a bench/PH
  model) batters simulated per team has the same probable-vs-actual problem
  `src/features/starter.py` already solves for starting pitchers
  (`starter_is_probable` flag, actual-vs-probable fallback order). Lineups
  are usually not final until close to first pitch; a simulation run at
  prediction time would have to use the *probable* lineup (or a
  positional-average proxy when unknown) and flag that fact the same way, or
  accept legitimate uncertainty by simulating over multiple plausible
  lineups.
- Bullpen usage/availability within the simulated game (who's rested, who's
  unavailable from recent high-leverage work) has to be point-in-time-safe
  too — this is exactly what `src/features/bullpen.py` already computes at
  the rolling-feature level; a simulator would consume the same kind of
  pregame-only bullpen state rather than re-deriving it from in-simulation
  events.
- Park factors are static-ish (no leakage risk) but must be versioned by
  season, since park effects drift year to year, and a"current-year" park
  factor computed from data that overlaps the evaluation window would leak
  the same way a same-season team rolling stat would if not shifted.
- Any model layered *on top of* raw simulated outcomes (e.g., calibrating
  simulated win probability against realized results) must still respect
  chronological train/test splits — no random splits, no calibrating on data
  that spans into the evaluation fold, per the existing ML correctness rules
  in `AGENTS.md`.

Net: point-in-time correctness is not a smaller problem for simulation, it's
a *bigger surface area* — every new stat (batter rates, lineup, bullpen
availability, park factor vintage) needs the same discipline currently
applied to just team/starter/bullpen rolling features.

---

## 4. Where this sits in the existing architecture

**It does not replace `src/features/build.py`.** The Gold matrix build's job
is producing one row of point-in-time features per `(game_pk, home/away)` for
a classifier; a simulator needs a different, richer point-in-time input set
(per-batter and per-pitcher rate stats, lineup, bullpen state, park), so it
would need its own assembly step, not a bolt-on to `build_feature_matrix`.
`build_feature_matrix`'s own docstring is explicit that it is a "pure pivot"
over three pre-built component feature lists and deliberately does not
re-plumb raw inputs — a simulation input builder is architecturally the same
*kind* of thing (a new component builder), not a modification of the
existing aggregator.

Proposed shape, following the repo's existing module boundaries:

- `src/simulation/inputs.py` (or similar) — point-in-time batter/pitcher rate
  builders, analogous to `src/features/starter.py` / `bullpen.py`, but keyed
  by batter/pitcher and reading the new `silver.plate_appearances` table
  instead of `pitcher_appearances`.
- `src/simulation/engine.py` — the base-out state machine and the
  trial-loop (or vectorized-batch) Monte Carlo runner: given a lineup, a
  starter, bullpen, and park, simulate N games and return per-trial
  runs-scored-by-team, or an aggregated distribution.
- `src/simulation/markets.py` — reduces raw trial output into derived
  markets: `P(home_win)` = fraction of trials where home runs > away runs;
  run total distribution → over/under probabilities at arbitrary lines; run
  differential distribution → run-line/spread probabilities at arbitrary
  lines; optionally per-player prop distributions (e.g. `P(player gets a
  hit)`) directly from the same trials.

**Relationship to `src/models/` contract**: the locked XGBoost model exposes
`build_model` / `predict_proba` / `model_metadata` and plugs into the shared
`src/evaluation/runner.py` walk-forward harness. A simulation engine is *not*
a drop-in implementer of that contract — it doesn't `fit(X, y)` on a
feature matrix, it runs a stochastic process parameterized by rate stats.
It could be evaluated with the *same walk-forward discipline* (same fold
boundaries from `src/evaluation/splits.py`, same primary metrics from
`src/evaluation/calibration.py` — log loss/Brier/ECE per ADR-006/ADR-003) by
treating "simulate this game 10,000 times, take the win fraction" as a
probability estimator, but it would need its own thin adapter rather than
reusing `run_evaluation`'s `build_model(random_state)` → `fit`/`predict_proba`
sklearn-shaped call sequence unmodified.

**Complements, does not compete with, the locked classifier — at least not
initially.** ADR-006 locked XGBoost/expanding-window/uncalibrated as the V1
methodology and explicitly reserves methodology changes for "a post-V1 or V2
decision with a new untouched evaluation policy." A simulation-derived
`P(home_win)` is a second, independently-produced probability estimate; the
natural first use is as an **alternative candidate to be compared against**
the locked model using the same primary metrics (not a replacement decided
in advance), or as a source for markets XGBoost was never built to answer
(run totals, spreads, props) while XGBoost continues to own the moneyline.
`MARKET-002` (backlog: persisted market-relative report/ROI) and the
existing `src/market/engine.py` no-vig/edge machinery are the natural
consumers of simulation-derived probabilities for those secondary markets —
the edge/EV math in `engine.py` is market-agnostic (American odds in,
implied probability out) and does not care whether `p_model` came from
XGBoost or from a simulation trial fraction.

---

## 5. Implementation risks and real cost

**Computational cost.** A daily slate is small (order 10-15 games), so the
concern isn't slate size, it's trials × plate-appearances-per-game. FanGraphs'
ZiPS runs on the order of a million trials for full-season standings; a
single-game moneyline/total/spread distribution needs far fewer trials to
converge (tens of thousands is typical for stable tail estimates on run
totals). Rough budget: 15 games × 20,000 trials × ~75 PAs/game/team × 2 teams
≈ 45M simulated PA draws/day. That is fine for a vectorized/batched
implementation (numpy array ops over trials, or a compiled loop) but would be
uncomfortably slow as naive per-trial Python object loops — this is a real
engineering cost, not just "write a for loop," and is the single biggest
implementation risk after the data gap in §2. The analytic Markov-chain
solve from §1 is a much cheaper fallback for expected-value markets (run
total mean) but doesn't by itself give the full outcome distribution a
props/spread market needs without still sampling or convolving.

**Calibration/validation approach.** Same walk-forward discipline this repo
already applies to the classifier, not a new invention:

- Hold out chronologically (same fold structure as `src/evaluation/splits.py`),
  simulate each held-out game N times using only pre-game rate stats, compare
  the simulated win-probability distribution's log loss/Brier/ECE against the
  locked XGBoost model's, using the same primary-metric ordering ADR-003
  established.
- For run totals/spreads specifically, validate the simulated distribution's
  calibration the same way — e.g. bucket games by simulated P(over line) and
  check realized over-rate per bucket, the run-total analogue of the ECE
  check `src/evaluation/calibration.py` already does for moneyline.
- The 2026-holdout-untouched-once policy (ADR-006/ML-010) would need an
  explicit new ADR decision about whether a simulation-derived model gets its
  *own* single untouched look at 2026 or is evaluated only on 2021-2025 folds
  until a V2 policy is accepted — this should not be assumed, it should be a
  deliberate decision recorded the way ADR-006 recorded the classifier's.

**How large a lift, honestly.** This is substantially bigger than the
current single-classifier pipeline, not a variant of it:

- New Bronze ingestion source (play-by-play), a brand new Silver table, and
  the first-ever *batter*-keyed data and features in this codebase (today
  batter identity does not exist anywhere in Silver).
- A new rate-estimation methodology (log5/odds-ratio or a small multinomial
  model per outcome category) that has no analogue in the current codebase —
  the closest existing thing, `src/features/starter.py`'s rolling rate
  features, computes simple rolling means, not a matchup-combination model.
- A new stochastic engine with its own performance profile and its own
  validation harness, evaluated against the same metrics but not reusable
  through the existing `src/evaluation/runner.py` model-contract path
  without a bespoke adapter.
- Genuinely new domain modeling decisions the classifier never had to make:
  lineup construction/uncertainty, in-game bullpen sequencing/leverage
  decisions, base-running and defense effects, extra innings handling.

A reasonable framing: the current classifier is "aggregate rolling stats in,
one boosted-tree probability out" — a few weeks of work at this repo's
demonstrated pace (FEAT-001 through ML-010 in the existing task graph). A
faithful PA-level Monte Carlo simulator is closer to standing up a second,
parallel data/feature/model subsystem from scratch, most realistically
scoped as its own multi-task graph rather than a single task. The
game-level-only alternative from §1/§2 (simulate final score margins from a
fitted distribution over the *existing* Gold features, no new ingestion) is
a legitimately smaller and faster path to "some secondary-market coverage
from one process" if that is the actual near-term goal, at the cost of not
supporting player props (which need batter-level granularity no matter what).

---

## 6. Proposed task breakdown (backlog only — nothing scheduled)

No existing prefix cleanly owns this: `DATA-` covers ingestion, `FEAT-`
covers point-in-time feature builders for the existing classifier, `ML-`
covers classifier training/evaluation, `MARKET-` covers odds/edge math on top
of a model's probability. A Monte Carlo simulator spans all four and is a
distinct enough subsystem (new data granularity, new stochastic engine, its
own validation harness) to warrant a new prefix: **`SIM-`**. Two prerequisite
ingestion tasks stay under the existing `DATA-` prefix since they are
ordinary Bronze/Silver ingestion work, just like `DATA-002`/`DATA-005` were
for schedule/game-detail data.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-018 | ingest MLB Stats API play-by-play (plate-appearance-level) Bronze payloads for games already covered by game-detail backfill |
| DATA-023 | backlog | DATA-022 | normalize `silver.plate_appearances` (batter/pitcher/base-out-state/outcome, point-in-time contract) |
| SIM-001 | backlog | DATA-023 | point-in-time batter rate-stat builder (first batter-keyed features in this repo, shift-before-roll) |
| SIM-002 | backlog | SIM-001, FEAT-003 | plate-appearance outcome probability model (log5/odds-ratio or multinomial) combining batter, pitcher, park |
| SIM-003 | backlog | SIM-002 | base-out state machine + single-game Monte Carlo trial engine (`src/simulation/engine.py`) |
| SIM-004 | backlog | SIM-003, ML-009 | walk-forward calibration/validation of simulated win probability vs. realized results and vs. the locked XGBoost model, using ADR-003 primary metrics |
| SIM-005 | backlog | SIM-003, MARKET-001 | derive run-total/run-line probabilities from simulation trials and feed `src/market/engine.py`'s edge/EV math |
| SIM-006 | backlog | SIM-004 | ADR decision: whether/how a validated simulation complements or supersedes ADR-006's locked classifier, and 2026-holdout policy for the simulation path |

A smaller, ingestion-free alternative worth keeping on the table alongside
the full graph above:

| Task | Status | Depends on | Notes |
|---|---|---|---|
| SIM-000 | backlog | FEAT-004 | game-level (non-PA) Monte Carlo: simulate final score margins from a fitted distribution over the existing Gold matrix, no new ingestion; smaller scope, no player props, faster path to run-total/spread coverage |
