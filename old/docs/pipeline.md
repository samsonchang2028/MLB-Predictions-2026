# Pipeline Plan

## Historical Backfill Pipeline

```text
season range
   |
fetch schedules
   |
fetch game/player/team data
   |
save Bronze JSON
   |
normalize Silver tables
   |
build point-in-time Gold rows
   |
quality checks
```

Historical data should be rebuildable from Bronze where possible.

## Training Pipeline

```text
load Gold training table
   |
select experiment configuration
   |
create chronological folds
   |
train model per fold
   |
calibrate if configured
   |
score validation period
   |
aggregate metrics
   |
save experiment results
   |
optionally train selected final artifact
```

## Daily Prediction Pipeline

```text
pull today's schedule
   |
identify eligible games
   |
update stats
   |
fetch probable starters
   |
fetch current odds
   |
build prediction-time features
   |
load production model
   |
produce probabilities
   |
convert odds to no-vig market probabilities
   |
calculate edge
   |
write immutable prediction record
   |
refresh Streamlit view
```

## Post-Game Pipeline

```text
fetch completed game results
   |
match outcomes to predictions
   |
update realized result fields
   |
recompute performance dashboards
```

## Prediction Timing

V1 should use a clearly defined prediction timestamp policy.

Example initial policy:

- generate one official prediction snapshot per game on game day after probable starters and odds are available

Future versions may support multiple snapshots such as morning, afternoon, and pre-first-pitch.

## Idempotency

Jobs should be safe to re-run.

Examples:

- raw snapshots may append with retrieval timestamp
- normalized game tables should upsert deterministically by natural key
- prediction records should never silently overwrite a prior official prediction

## Failure Handling

Log and surface:

- API failures
- malformed responses
- missing probable starter
- unmatched odds event
- incomplete game
- stale odds snapshot

Do not silently substitute future data when current data is missing.
