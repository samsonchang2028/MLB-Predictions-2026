# Testing Plan

## Priority
The most important tests protect against data leakage and incorrect financial/market logic.

## Unit Tests

### Odds Conversion
Test:

- positive American odds
- negative American odds
- implied probability
- no-vig normalization

### Rolling Features
Test that:

- current game is excluded
- first games produce expected missing values
- ordering by time is enforced
- rolling window boundaries are correct

### Differential Features
Test sign convention:

```text
home - away
```

### Game Matching
Test:

- normal game
- postponed game
- doubleheader
- same teams on same date
- rescheduled game

## Leakage Tests

Create synthetic data where future values are deliberately extreme.

Assert that changing a future game cannot alter features for an earlier game.

Also test that:

- current target cannot enter feature columns
- postgame stats cannot enter pregame feature row
- odds snapshot timestamp is before prediction timestamp/first pitch according to policy

## Integration Tests

Test a small known date range end to end:

```text
raw fixture -> Silver tables -> Gold features -> model fit -> prediction
```

## Backtest Tests

Verify:

- chronological splits
- no overlapping future rows in train set
- payout calculations
- edge threshold logic
- aggregation by season/fold

## Smoke Tests

A minimal command should be able to:

1. load sample data
2. build features
3. fit Logistic Regression
4. score a held-out period
5. return metrics without error

## Data Quality Assertions

Fail loudly for:

- duplicate primary keys
- impossible timestamps
- missing teams
- home team equals away team
- feature rows after game completion if marked pregame
- target values outside {0,1}
