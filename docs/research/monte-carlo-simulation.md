# Monte Carlo simulation research

Status: research only. Nothing here changes ADR-006, the locked V1 moneyline
model, or the current Streamlit product behavior.

## Summary

Monte Carlo simulation could help this project in two different ways:

1. **Moneyline support**: produce an independent simulated `P(home_win)` that
   can be compared against the locked XGBoost moneyline model.
2. **New markets**: produce a full run distribution, which can answer totals
   and run-line questions more naturally than independent binary classifiers.

The practical recommendation is to start with a small **game-level simulation
prototype** over the existing Gold matrix before attempting a full
plate-appearance simulator. The full version is a major new subsystem because
this repo currently has no batter-level or plate-appearance-level Silver data.

## What Monte Carlo means for MLB

Baseball can be simulated as repeated random game events. The detailed version
models each plate appearance, advances runners through base/out states, repeats
for thousands of games, then reads the distribution of final scores.

Useful outputs:

- `P(home team wins)` for moneyline.
- `P(total runs > line)` for over/unders.
- `P(home margin > spread)` for run lines.
- Score-distribution summaries such as median total runs or blowout probability.

This is different from the current V1 classifier. The current model predicts
one probability directly: `P(home_win)`. A simulation tries to generate game
scores and derives probabilities from those simulated scores.

## External research anchors

- Retrosheet event files describe full play-by-play game events, which is the
  kind of source needed for a plate-appearance simulator:
  <https://www.retrosheet.org/eventfile.htm>
- Baseball half-innings are commonly modeled with 24 base/out states plus an
  inning-ending state:
  <https://medium.com/analytics-vidhya/markov-chain-baseball-models-31bd52c422d3>
- FanGraphs/ZiPS uses large Monte Carlo simulation for season projections,
  showing the technique is standard in public baseball forecasting:
  <https://blogs.fangraphs.com/the-official-and-hopefully-not-too-erroneous-2026-zips-projected-standings/>
- FiveThirtyEight's historical MLB forecasting data shows a lighter game-level
  forecast path can exist without full plate-appearance simulation:
  <https://github.com/fivethirtyeight/data/blob/master/mlb-elo/README.md>

## Current repo gap

Current Silver/Gold data is enough for a first game-level prototype, but not a
true plate-appearance simulator.

What the repo has now:

- completed games and scores,
- team rolling features,
- starter rolling features,
- bullpen rolling features,
- moneyline odds snapshots,
- final 2026 holdout evidence for the locked V1 moneyline model.

What a full plate-appearance simulator needs but the repo does not yet have:

- batter identities,
- batting order / lineup history as Silver data,
- one row per plate appearance,
- base/out state before and after each plate appearance,
- event outcome categories such as walk, strikeout, single, double, home run,
- park factor data,
- batter-vs-pitcher or batter/pitcher outcome-rate estimation.

Conclusion: a full plate-appearance Monte Carlo model is valuable but not a
small feature. It needs new data ingestion, new Silver tables, new features, and
new evaluation.

## Recommended path

### Phase 1: game-level simulation prototype

Use the existing Gold feature matrix and historical scores to model a
distribution over final scores or score margins. This is less precise than
plate-appearance simulation but fits the current repo.

Possible prototype:

- fit expected home runs and away runs from existing pregame features,
- sample final scores from a count distribution,
- derive:
  - moneyline probability,
  - total-runs probability,
  - run-line probability.

This should be treated as a research candidate, not a production model.

Pros:

- no new ingestion required,
- fast to prototype,
- can answer totals/run-line questions earlier,
- can be compared against the locked XGBoost moneyline model.

Cons:

- no player props,
- weaker lineup/player detail,
- score-distribution assumptions must be validated,
- may be less accurate than a real plate-appearance simulator.

### Phase 2: plate-appearance simulation

Add new play-by-play ingestion and simulate games at the plate-appearance level.

This is the long-term version if the goal is a serious simulation engine.

Pros:

- one internally consistent source for moneyline, totals, run line, and some
  player prop probabilities,
- can model lineup changes and pitcher/batter matchups directly,
- more explainable baseball mechanics.

Cons:

- large new data pipeline,
- first batter-level feature system in this repo,
- harder point-in-time correctness surface,
- more compute and validation work,
- needs a new ADR before any methodology lock.

## Point-in-time rules

Simulation does not relax the project’s leakage rules.

Every simulation input must be available before prediction time:

- batter rates must be shifted before rolling,
- pitcher rates must exclude the current game,
- lineups must be known pregame or represented as unknown/probable,
- odds snapshots must be before prediction cutoff,
- park factors must be versioned so future same-season information is not used,
- calibration must use chronological train/calibration/test boundaries.

The same 2026 holdout discipline applies. Do not use 2026 to tune a simulation
unless a new accepted ADR defines a post-V1 evaluation policy.

## How it could benefit moneylines

Monte Carlo could improve or support moneyline predictions by:

- producing a second independent probability estimate,
- exposing score-distribution uncertainty rather than only a single class
  probability,
- identifying cases where XGBoost and simulation disagree,
- improving explainability: e.g. "projected low-scoring one-run game" vs.
  "projected high-variance slugfest",
- allowing model blends only after proper walk-forward validation.

Do not blend it into V1 automatically. Any blend would be a new methodology.

## How it could benefit totals and run lines

Totals and run lines need a run distribution. A simulation naturally provides
one.

From simulated scores:

- over probability = share of trials where `home_runs + away_runs > total_line`,
- under probability = share of trials where `home_runs + away_runs < total_line`,
- run-line cover probability = share of trials where margin covers the offered
  spread.

This is cleaner than training separate unrelated classifiers for moneyline,
totals, and run line, because the probabilities come from the same simulated
score distribution.

## Proposed task graph

These are backlog candidates only. IDs are proposed, not reserved.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| SIM-000 | backlog | FEAT-004, ML-010 | game-level simulation prototype using existing Gold features and historical scores; no new ingestion |
| SIM-001 | backlog | SIM-000 | validate simulated moneyline probability against locked XGBoost using log loss, Brier, calibration, ROC-AUC secondary |
| SIM-002 | backlog | SIM-000, MARKET-001 | derive totals/run-line probabilities from simulated score distributions |
| DATA-022 | backlog | DATA-018 | ingest play-by-play / plate-appearance payloads for completed games |
| DATA-023 | backlog | DATA-022 | normalize `silver.plate_appearances` with batter, pitcher, base/out state, and outcome category |
| SIM-003 | backlog | DATA-023 | point-in-time batter and pitcher outcome-rate builders |
| SIM-004 | backlog | SIM-003 | plate-appearance Monte Carlo engine |
| SIM-005 | backlog | SIM-004 | walk-forward validation and calibration of simulation outputs |
| SIM-006 | backlog | SIM-005 | ADR for whether simulation remains research, complements V1, or becomes a V2 methodology candidate |

## Recommended next action

If approved, start with `SIM-000`. It is the smallest useful step because it
uses existing data and can prove whether score-distribution modeling is worth
more investment before building a full play-by-play subsystem.

