# Feature Engineering Plan

## Core Rule
All features must be point-in-time safe.

For a game at time `T`, a feature may only use information available before the chosen prediction timestamp at or before `T`.

## Feature Groups

### Team Strength

- season win percentage before game
- run differential before game
- runs scored per game
- runs allowed per game

### Recent Form

Rolling windows to test:

- 7 games
- 14 games
- 30 games

Candidate metrics:

- win percentage
- run differential
- runs scored
- runs allowed
- OPS/OBP/SLG where obtainable cleanly
- strikeout rate
- walk rate

### Starting Pitcher

Candidate features:

- season ERA
- season WHIP
- K%
- BB%
- K-BB%
- HR/9
- innings per start
- pitches per start
- days rest
- ERA/WHIP over recent starts
- strikeouts over recent starts
- walks over recent starts

Avoid assuming ERA is sufficient; rate and workload features should be included where feasible.

### Bullpen

Candidate features:

- bullpen ERA over recent window
- bullpen WHIP over recent window
- bullpen innings last 1 day
- bullpen innings last 3 days
- bullpen pitches last 3 days if obtainable
- high-leverage usage proxy later

### Schedule / Fatigue

- days since previous game
- games in last 7 days
- consecutive game days
- doubleheader context

### Context

- home-field indicator
- venue/park identifier
- park factors later

## Differential Features

Create both raw home/away features and explicit differences where useful.

Examples:

- starter_era_diff
- starter_kbb_diff
- bullpen_era_diff
- bullpen_workload_diff
- offense_ops_diff
- run_diff_diff
- rest_days_diff

A consistent sign convention should be used, preferably:

```text
home value - away value
```

## Rolling Feature Safety

Any rolling statistic based on game results must exclude the current game.

Conceptually:

```python
series.shift(1).rolling(window).mean()
```

not:

```python
series.rolling(window).mean()
```

## Missing Data

Document a consistent strategy for:

- season openers
- pitchers with little MLB history
- rookies
- newly traded players
- missing probable starters

V1 should favor simple, explicit imputation with missingness indicators where useful.

## Feature Versioning

Feature definitions should be versioned. A prediction record should store the feature version used.

Example:

```text
feature_version = v0.1
```
