# Research: MLB Run Line (Spread) Market Support

Status: research only, no code changed. Written against the codebase as of
2026-08-14 (main branch, ADR-006 V1 lock in effect: single XGBoost `home_win`
classifier, `src/features/build.py` Gold matrix, `src/market/engine.py`
two-way no-vig engine, `src/ingestion/odds/snapshots.py` parsing only `h2h`).

## 1. What an MLB run line actually is

The run line is baseball's version of a point spread, expressed as
`-1.5` (favorite must win by 2+ runs) / `+1.5` (underdog can lose by 1 run
and still "cover"). Unlike NFL/NBA spreads, which are computed per-matchup
from team strength, the MLB run line is **fixed at 1.5 runs for the
overwhelming majority of games** — the number essentially never moves, the
book instead moves the *price* (American odds) on each side to balance the
market. This is a direct consequence of MLB's low-scoring, one-run-heavy
distribution: roughly 28% of MLB games are decided by exactly one run, which
is enough one-run density that a fixed 1.5 line always splits the outcome
space usefully without needing a wider line for blowout-prone matchups.

Sources agree the line is "nearly always," "almost always" 1.5 runs and
consistently call out that a half-run line guarantees no pushes (a run
total can't land on a half-integer). None of the sources found gave a
frequency statistic for exceptions. Anecdotally / by industry convention,
some books post an **alternate** run line (e.g. -2.5 for a big favorite,
or occasionally -1.5/+2.5 in mismatches) as a *secondary* market, but the
Odds API's primary `spreads` market key for baseball should be checked
empirically once ingested — this document does not hardcode "the line is
always exactly 1.5" as a code assumption; see §2 and §3.

**Practical implication for this repo:** because the line is nearly
invariant, run-line prediction is much closer to "predict
`P(home team wins by 2+ runs)` [equivalently `P(away team wins by 2+ runs)`
for the away side]" than to a genuinely variable-line spread-regression
problem. That reframes it as a second binary classification target
alongside `home_win`, not a regression problem requiring a full margin
distribution — *if* the line stays 1.5. The ingestion layer must still
record the actual `point` value per snapshot rather than assuming 1.5, both
because the repo's data rules forbid unverifiable assumptions and because
alternate/non-standard lines do occasionally appear and must not corrupt a
"covered value" computed under a hardcoded 1.5 assumption.

Sources:
- [Why is the Run Line Always 1.5 for Betting MLB Games? - Bleacher Nation](https://www.bleachernation.com/betting/2026/05/23/why-is-the-run-line-always-1-5/)
- [Run Line in Baseball Explained: Point Spreads for MLB Betting](https://www.sportsbettingdime.com/guides/how-to/baseball-run-line-betting/)
- [MLB Run Lines: Why Baseball's Version of the Spread Is Tough](https://www.sportsgamblingpodcast.com/2026/08/11/mlb-run-lines-why-baseballs-version-of-the-spread-is-tough/)
- [What Does -1.5 Run Line Mean in MLB Betting? - BetMGM](https://sports.betmgm.com/en/blog/mlb/what-does-minus-1-5-run-line-mean-in-mlb-betting-bm23/)

## 2. Ingestion changes: `src/ingestion/odds/snapshots.py`

Current state (read in full): `parse_the_odds_api_moneylines` iterates
`book["markets"]` and does `if market.get("key") != "h2h": continue` (line
70), discarding everything else in the same event/bookmaker payload,
including `spreads` and `totals`. Outcomes are validated as `{name, price}`
only (`_required_text(outcome.get("name"), ...)`, then an American-price
check) — there is no code path anywhere that reads an outcome's `point`
field.

The Odds API's `spreads` market for baseball has this outcome shape (per
The Odds API's documented schema, consistent with NFL/NBA spreads outcomes):

```json
{
  "key": "spreads",
  "outcomes": [
    {"name": "<home_team>", "price": -115, "point": -1.5},
    {"name": "<away_team>", "price": -105, "point": 1.5}
  ]
}
```

This is structurally identical to the `totals` market's outcome shape
(`{"name": "Over"|"Under", "price": ..., "point": <line>}`) in that both
carry a `point` field the current parser has no concept of, while `h2h`
outcomes never carry one. **Combining ingestion for `spreads` and `totals`
into one task is plausible and probably the right call**: both need (a) a
new `_required_number`-style validator for `point` (the existing
`_required_text` validator doesn't fit a float), (b) a new bronze table with
an extra `point` column and a `(source, source_event_id, bookmaker, market,
outcome, point, snapshot_timestamp)`-shaped primary key (the run-line PK
needs `point` in the key because a book can simultaneously offer the
standard -1.5 line and an alternate -2.5 line — same `outcome` side, two
different points), and (c) the same team-name-to-side mapping logic already
in `parse_the_odds_api_moneylines` (spreads/totals outcomes for
spreads use team name like `h2h`; totals outcomes use `"Over"/"Under"`
instead of team name, so the two markets aren't *identical* in shape, just
both `point`-bearing — worth confirming precisely once the totals research
lands before deciding whether one shared helper or two thin ones is
cleanest).

Concretely, this is not a `home_american`/`away_american`-only extension —
the bronze schema shape changes (new `point` column, market discriminator
column since `bronze.odds_moneyline_snapshots` is currently h2h-only and
named for it). The likely path is a new sibling table
(`bronze.odds_spread_snapshots` or a combined `bronze.odds_snapshots` with a
`market` column covering h2h/spreads/totals) rather than bolting `point`
onto the existing moneyline table, to avoid a nullable column that's
meaningless for 100% of `h2h` rows. That table-shape decision should be made
once, at the point spreads+totals ingestion is actually scoped, not
preemptively here.

Cross-reference: this observation overlaps with the parallel totals-market
research; if that document independently converges on "totals ingestion
needs `point`-field parsing too," the two should likely be filed as one
combined ingestion task (see §6) rather than duplicated implementations.

## 3. Target/label: does the home team cover the line?

The target is **not** derivable from `home_win` alone — a team can win by 1
run (loses the run line at -1.5) or win by 2+ runs (covers it). The label
needs the actual **margin of victory**, which requires final scores, not
just win/loss.

Confirmed by reading `src/transforms/silver.py`:
`silver.team_game_statistics` (DDL at lines 704-719) has, per
`(game_pk, team_id)`:

```sql
CREATE TABLE IF NOT EXISTS silver.team_game_statistics (
    game_pk BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    side VARCHAR NOT NULL CHECK (side IN ('home', 'away')),
    score INTEGER,          -- post-game outcome; not a pregame feature (ADR-002)
    is_winner BOOLEAN,      -- post-game outcome; not a pregame feature (ADR-002)
    ...
    PRIMARY KEY (game_pk, team_id)
)
```

So `score` exists per side and a game's margin is
`home_score - away_score`, computable by joining the home and away rows for
a `game_pk` (the same pattern `src/features/build.py::_home_win` already
uses to derive `home_win`, lines 429-447: it looks up both sides via
`results_index`, prefers `is_winner`, and falls back to a `score`
comparison).

Proposed label: `home_covers_run_line = (home_score - away_score) > 1.5`,
i.e. `home_score - away_score >= 2`, computed once from `silver.team_game_
statistics` scores and **reusable regardless of what line was actually
offered on any given day**, exactly the way `home_win` is reusable
regardless of moneyline price. This should live next to `_home_win` as a
sibling pure function (`_home_covers_run_line` or similar), following the
target-isolation contract already established in `build.py`
(§ "Target-isolation contract": labels live only under `row["target"]`,
never in `features`).

Important precision point the task instructions call out: **do not hardcode
"the line is always 1.5" into the label-derivation code** as if it were a
market fact rather than a modeling choice. The label above is a genuine
modeling decision ("we're training a model against the *standard* run
line"), not an empirical fact read from data — it should be an explicit,
named constant (e.g. `STANDARD_RUN_LINE = 1.5`) so a future task can retrain
against a different fixed line without archaeology, and so the code is
honest that it assumes the standard line rather than reading it from each
game's actual offered spread. Whether evaluation should instead join against
the actual ingested `point` per game (once §2 ingestion exists) — to score
"did the model's fixed-line label match what was actually offered that
day" — is an open design question for the eval task, not the label-derivation
task.

## 4. Features: is the existing Gold matrix sufficient?

Genuinely open question — not something to assert confidently either way
without an experiment.

**Case for "the existing matrix is probably sufficient as a first cut":**
`src/features/build.py` already assembles team/starter/bullpen features
(the same signal families that predict `home_win`) and nothing in the
column set is win/loss-clipped — run differential, ERA/FIP-style pitching
metrics, bullpen strength, etc. (whatever the underlying builders in
`src/features/team.py`/starter/bullpen expose) are continuous quantities
that plausibly correlate with margin, not just win probability. A team
that's a big favorite by the model's `home_win` probability is, on priors,
also more likely to win by 2+. If that holds, a run-line classifier could
reuse `build_feature_matrix`'s output unchanged, just swapping the target
key (`home_covers_run_line` instead of `home_win`) — cheapest possible
version, no new FEAT- task needed to *ship* a first run-line model.

**Case against "as-is is enough":** win probability and margin are
correlated but not the same signal. A team can be a heavy favorite (high
`P(home_win)`) via a dominant starter who reliably wins 1-run efficient
games, or via a lineup that blows teams out — those are different margin
profiles with similar win probability. Features that specifically move win
probability without moving expected margin (e.g. bullpen closer quality in
low-leverage/close-game situations) could dilute a margin classifier's
signal. There may be real value in bullpen-depth/blowout-suppression
features, park factors (run-scoring environment, e.g. Coors Field
systematically inflates both teams' scoring and therefore margin
variance), or offense/defense-differential features not currently
prioritized because they don't move `home_win` much.

**Conclusion:** ship the "reuse as-is" version first (cheapest, and the
correlation argument is plausible), but this is an empirical question that
should be settled by comparing a run-line model's log loss/Brier/calibration
against a naive baseline (e.g. always predict the training-set base rate
of `home_covers_run_line`, or a simple function of the existing
`home_win` probability) before claiming the reused features are "good
enough." Do not skip that comparison — it is exactly the kind of
unverified assumption AGENTS.md's ML correctness rules and this project's
evidence-first culture (ADR-006 required expanding/rolling comparison
before locking a model) would flag.

## 5. Point-in-time / evaluation discipline

No new framework is needed here — the existing walk-forward apparatus
(`ML-004` framework, `ML-004A` per-fold predictions, `ML-005`/`ML-006`
window comparisons, `ML-008` calibration comparison, all keyed off
chronological splits per AGENTS.md's non-negotiable rules) applies unchanged
to a run-line target. The label (`home_covers_run_line`) is derivable at
the same point-in-time boundary as `home_win` (post-game, from certified
results, never used as a pregame feature), and features remain the existing
point-in-time-safe Gold columns (§4). A run-line model is a second
classifier trained on the same certified build with a different target
column — not a new data-pipeline stage.

Connection to Monte Carlo / run-scoring simulation: a full run-scoring
simulation (simulate each team's runs scored via some generative process,
e.g. inning-by-inning or box-score-level Monte Carlo draws) would naturally
produce a *distribution* over the final score margin for a given game. From
one simulated distribution you get, for free:
- moneyline: `P(home_runs > away_runs)`,
- run line: `P(home_runs - away_runs > 1.5)` (or whatever line is queried),
- totals: `P(home_runs + away_runs > line)`.

That is the more principled long-run architecture (one generative model, three
derived markets, internally consistent by construction) versus the
short-run pragmatic path this document describes (three independent
binary/point classifiers trained separately, which can produce mutually
inconsistent implied probabilities across markets — e.g. run-line and
moneyline probabilities that don't logically nest). The three-classifier
path is the cheap, ship-able version; full run-scoring simulation is a
substantially larger, separate research/engineering effort (its own
research topic per the task context) and should not be a prerequisite for
shipping a run-line classifier now. Flag this trade-off explicitly in any
future ADR that locks a run-line methodology, the way ADR-006 flagged
calibration trade-offs.

## 6. Proposed task breakdown

Following `tasks/index.md`'s table style and this repo's `tasks/<ID>-
<slug>.md` convention (DATA- ingestion, FEAT- feature engineering, ML-
modeling, MARKET- odds engine). All new tasks start at Status `backlog`.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-003 | ingest `spreads` (run line) + `totals` markets from The Odds API in one task — both need new `point`-field outcome parsing and a new bronze schema (market discriminator + `point` column); combine with totals ingestion rather than splitting, per the shared-shape overlap noted in §2. Flag: split into two tasks only if the parallel totals research concludes the outcome shapes diverge enough to make a shared parser not worth it. |
| FEAT-007 | backlog | DATA-022 | (only if needed) add margin/park-factor-oriented features found necessary by ML-012's feature-sufficiency check in §4; otherwise this task is skipped entirely and ML-012 reuses `build_feature_matrix` unchanged. |
| ML-012 | backlog | FEAT-004 | new `home_covers_run_line` label (`home_score - away_score > 1.5`, standard-line constant, derived from `silver.team_game_statistics` scores per §3) + a first run-line classifier reusing the existing Gold feature matrix; report log loss/Brier/calibration against a naive base-rate baseline to settle the §4 feature-sufficiency question before any further investment. |
| MARKET-003 | backlog | MARKET-001, DATA-022 | extend the market engine for the run-line's two-way structure (cover/not-cover at whatever `point` was actually offered, not just win/loss); likely a `no_vig_two_way`-shaped sibling function since the run line is still a two-way market, just outcome-conditioned on margin vs. a `point` instead of margin vs. 0. |

Dependency notes:
- DATA-022 is the hard prerequisite for MARKET-003 (needs real offered
  `point`/price data) but *not* for ML-012 (the label is computed from
  already-ingested `silver.team_game_statistics` scores, independent of
  whether spread odds have been ingested — a run-line model can be trained
  and evaluated on model-quality metrics alone before any spread-odds
  ingestion exists, exactly as `home_win` classification long predated
  `MARKET-001`'s edge-vs-market comparison).
- FEAT-007 is conditional and should not be scheduled speculatively; per
  AGENTS.md ("do not add abstractions for hypothetical future
  requirements"), only open it if ML-012's baseline comparison in §4 shows
  the reused Gold matrix is measurably insufficient.
- A future ADR (methodology lock for the run-line model, mirroring
  ADR-006) would follow ML-012's evidence, not precede it.
