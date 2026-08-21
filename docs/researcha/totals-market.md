# Research: Totals (Over/Under Runs) Market Support

Status: research only, no code changed. Grounded in the current repo state as of
this writing (`snapshots.py`, `engine.py`, `build.py`, ADR-006, `tasks/index.md`).

## 0. Summary

The repo today predicts one thing — P(home win) — from one classifier trained on
one label (`home_win`, derived in `src/features/build.py` from `home_score` vs
`away_score`). Totals is a genuinely different prediction target (runs scored,
not win/loss), so this is closer to a second product line sharing the existing
Gold feature matrix and evaluation discipline than a small add-on. The ingestion
and market-math pieces are small, mechanical extensions of what already exists.
The modeling piece is the real work, and its shape depends on a decision this
doc surfaces but does not make: classifier-per-line vs. a runs-distribution
approach that would also serve run-lines and moneyline (see §2).

## 1. Ingestion changes (`src/ingestion/odds/snapshots.py`)

Today `parse_the_odds_api_moneylines` hard-filters to one market:

```python
for market_index, market in enumerate(markets):
    if market.get("key") != "h2h":
        continue
```

and each outcome is validated against `home_team`/`away_team` names (lines
77-92), with no concept of a line/point value anywhere in the row shape (bronze
table `bronze.odds_moneyline_snapshots`, `outcome CHECK (outcome IN ('home',
'away'))`).

**Totals outcome shape** (confirmed against The Odds API's public v4 docs):

```json
{
  "key": "totals",
  "outcomes": [
    {"name": "Over", "price": -110, "point": 8.5},
    {"name": "Under", "price": -110, "point": 8.5}
  ]
}
```

Both outcomes carry the *same* `point` (the total-runs line), unlike `spreads`
where home/away carry opposite-signed points. `name` is `"Over"`/`"Under"`, not
a team name, so the existing "does the outcome name match `home_team` or
`away_team`" validation (lines 80-87) doesn't apply and needs a parallel
`side in {"Over", "Under"}` check instead. This is a fairly standard
sportsbook-API convention (mirrors DraftKings/FanDuel-style feeds too), but
treat the exact field names as **should-be-correct-but-verify-against-a-live-
payload** rather than repo-internal ground truth, since it comes from external
docs, not this codebase.

A book can and does offer **multiple totals lines per event** (e.g. 8.5 and
9.5 simultaneously, or a line move over the day) — unlike moneyline, which has
exactly one home/away pair per book per snapshot. The current bronze primary
key is `(source, source_event_id, bookmaker, outcome, snapshot_timestamp)`,
which assumes at most one row per side per snapshot. A totals table needs
`point` in the key: `(source, source_event_id, bookmaker, outcome, point,
snapshot_timestamp)`, otherwise two different lines at the same instant would
collide or silently overwrite.

Concretely, this is **not** a change to `parse_the_odds_api_moneylines` in
place — the function name and the row shape (`outcome IN ('home', 'away')`,
team-name validation, no `point` column) are moneyline-specific by design. The
lazy path is a **new sibling function and a new bronze table**
(`parse_the_odds_api_totals` → `bronze.odds_totals_snapshots`), reusing every
shared helper in the file (`_required_text`, `_timestamp`, `_objects`,
`OddsDataError`) as-is. Bolting an `if market_key == "totals": validate point
instead of team name` branch into the existing function would make one
function do two incompatible validation contracts — worse than two small
parallel functions.

## 2. What's the label? Why this isn't just "swap the target column"

Current target: `home_win` — a single boolean, one per game, no dependence on
any externally offered line. The model outputs one number, P(home win), that
means the same thing regardless of what price a book posts.

Totals doesn't have that property. The natural raw label is **total runs
scored** (`home_score + away_score`, computable today from the exact same
`results` structure `_home_win` already reads in `build.py:429-446` — no new
data source needed). But total runs is a **continuous/count outcome**, not a
binary one, and what a totals *bettor* actually needs is P(over) or P(under)
**relative to whatever line the book is offering that day** — which moves
game to game and can differ book to book. Two runs-scored architectures follow
from that, with different scope:

- **(A) Classifier-per-line.** Train P(total > line) directly, either as one
  model conditioned on `line` as a feature, or as several fixed-line models.
  Narrower, single-purpose, closer in spirit to the current XGBoost classifier
  — reuses the ML-003/ADR-006 pattern almost directly. Only serves totals.
- **(B) Runs-distribution / simulation.** Model each team's expected runs (or
  simulate full games, e.g. Monte Carlo over per-team scoring), producing a
  distribution over total runs. P(over) for *any* line is then a query against
  that distribution (`P(total > line)`), not a retrain. This is the same
  underlying object that would also answer run-line (spread) questions and can
  be collapsed to P(home win) for moneyline — i.e. it's a superset, not a
  totals-specific thing.

This repo has a parallel research effort into Monte Carlo game simulation. If
that direction is adopted, totals should very likely be a *consumer* of that
simulation output rather than its own model family — building (A) first and
then (B) later would mean throwing away (A)'s training work. If simulation is
not adopted (or is far out), (A) is the standalone lazy path: smaller, ships
totals alone, doesn't block on an unrelated bet. This doc does not resolve
that dependency — it's flagged in the task table below (§5) as the fork point.

Either way: this is a **new model type**, not a retarget of the existing
XGBoost binary classifier. `home_win` is a fixed-shape label; a totals label
either needs the offered line as an extra input (A) or needs a different
objective entirely — regression / distributional, not classification (B).

## 3. Feature reuse

The Gold matrix (`src/features/build.py`, `feature_columns()`) is
target-agnostic by construction — the docstring is explicit that `results` are
consumed *only* to derive `home_win`, kept out of `feature_columns` (line
23-25, "target-isolation contract"). That means the **same feature build is
reusable as-is** for a totals model; nothing in `build.py` needs to change to
support a different label. What changes is what gets attached as `target`:
today it's `{"home_win": bool | None}` (line 223-224); a totals variant would
need something like `{"home_win": ..., "total_runs": int | None}` computed
from the same `results_index` (or, cleanly, a separate label-derivation
function reusing the existing `home_score`/`away_score` lookups without
touching the shared feature-building path — avoids widening the row shape for
every consumer that only wants `home_win`).

On relevance: starter/bullpen ERA-family features (FEAT-002, FEAT-003) are
*directly* useful for totals — they're literally run-prevention signals,
arguably more directly predictive of total runs than of win/loss. Team-level
offensive features (FEAT-001) matter more here too (moneyline cares about the
run differential's sign; totals cares about the sum's magnitude). No new
feature *engineering* work is implied by this research — the gap is entirely
on the label/model side, not the feature side.

## 4. Evaluation discipline

No exceptions requested or found: same `src/evaluation/` walk-forward
machinery, same chronological-split rule, same 2026-holdout lock (AGENTS.md:
"The 2026 season is the final untouched holdout unless an accepted ADR changes
that policy" — a totals model evaluated against 2026 is still spending that
one holdout look, so it should happen *after*, not in parallel with,
uncoordinated experimentation), same point-in-time discipline for both
features and the totals odds line itself (a totals prediction must not see a
line snapshot dated after its own prediction timestamp — this is exactly the
`snapshot_is_pregame_valid` guard already in `engine.py`, reusable unchanged).

Primary metric choice depends on which architecture from §2 is picked:

- **(A) classifier-per-line**: log loss / Brier / calibration on P(over) at
  the *specific offered line* — directly parallel to today's ADR-003 ordering
  (log loss, Brier, calibration primary; ROC-AUC, accuracy secondary). Clean
  fit with existing tooling in `src/evaluation/calibration.py`.
  Complication: pooling games with different lines into one log-loss number is
  apples-to-oranges unless the line is itself a model input — a plain
  "accuracy vs. the day's line" number is *not* comparable across games with
  different totals the way `home_win` accuracy is comparable across games with
  different moneylines, because the totals line itself is engineered to sit
  near P(over)=0.5, so its behavior is closer to calibration-quality than to
  discriminative power.
- **(B) runs-distribution**: a continuous-outcome metric on predicted total
  runs (MAE, RMSE, or CRPS against actual `total_runs`) is the natural primary
  metric for the distribution itself; log loss on the *derived* P(over) for
  whatever line was offered becomes a secondary, market-facing metric layered
  on top. Tradeoff: MAE/RMSE score the mean of the distribution, not its
  shape, so a distributional metric (e.g. CRPS, or log loss of the actual
  outcome under the predicted distribution) is the closer match to this
  repo's stated preference for probability quality over point accuracy — but
  it's a genuinely new metric family, nothing in `src/evaluation/` computes
  it today.

Either way, whichever primary metric is picked needs the same "decide before
looking at 2026" discipline ADR-006 used for the moneyline lock — not a new
rule, just the existing one applied to a new target.

## 5. Proposed task breakdown

Matches `tasks/index.md` table style. IDs continue from the current highest
per prefix (DATA-021, MARKET-002, ML-011 are the current top backlog/done
entries). All new tasks start `backlog`.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-003 | ingest `totals` market (Over/Under + `point`) from The Odds API into a new `bronze.odds_totals_snapshots` table; sibling to `parse_the_odds_api_moneylines`, not a change to it; PK must include `point` (multiple simultaneous lines per event) |
| DATA-023 | backlog | DATA-022, DATA-009 | totals odds -> `game_pk` mapping (reuse DATA-009's archive-mapping pattern) if historical totals odds are ingested, not just live snapshots |
| MARKET-003 | backlog | MARKET-001, DATA-022 | extend the no-vig engine for a two-way market priced against a line (`no_vig_two_way` already handles the two-outcome math; add an Over/Under variant that carries `point` through `NoVigMarket`/`MarketEvaluation` instead of `home_american`/`away_american`) |
| ML-012 | backlog | FEAT-004, DATA-022 | totals label derivation (`total_runs` from existing `results`, target-isolation contract preserved) + baseline classifier-per-line (architecture A, §2); **only if the Monte Carlo simulation task is not adopted first** |
| ML-013 | backlog | ML-012 or Monte Carlo simulation task (whichever lands) | totals-appropriate primary metric selection + ADR (log loss on P(over) vs. distributional metric, §4) and methodology lock, mirroring ADR-006's process for a new target |
| APP-006 | backlog | ML-013, APP-001 | surface totals predictions/edge on the daily board (net-new UI surface, out of scope for this research) |

Fork-point note for the orchestrator: if the Monte Carlo simulation research
lands a task first, ML-012 as scoped above (a totals-specific classifier)
becomes redundant — replace it with "wire totals queries against the
simulation's runs distribution" rather than training a second, narrower model.
This table assumes simulation is *not* yet decided; recheck before scheduling
ML-012.
