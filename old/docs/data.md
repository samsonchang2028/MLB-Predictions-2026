# Data Plan

## Primary Sources

### MLB Stats API
Target data:

- schedules
- final game results
- teams
- venues
- players
- probable starters
- team batting/pitching information
- pitcher game logs
- team game-level performance

### Odds Source
Target data:

- event identifier
- bookmaker
- market
- home team
- away team
- moneyline price
- snapshot timestamp
- event commence time

Historical odds availability may be limited by provider plan. The system should support current odds collection and continuous archival even if complete historical odds are unavailable initially.

## Bronze Layer

Store raw API responses with enough metadata to replay transformations.

Suggested partitioning:

```text
data/bronze/mlb/schedules/season=2024/date=2024-06-01/*.json
data/bronze/mlb/games/season=2024/*.json
data/bronze/odds/date=2026-08-06/*.json
```

Every raw record/snapshot should preserve:

- source
- retrieval timestamp
- request parameters
- API event/game identifier

## Silver Tables

### games

Suggested columns:

- game_pk
- game_date
- game_datetime
- season
- game_type
- status
- home_team_id
- away_team_id
- home_score
- away_score
- home_win
- venue_id
- doubleheader_flag
- game_number

### team_games

One row per team per game.

Suggested columns:

- game_pk
- team_id
- opponent_team_id
- is_home
- runs
- hits
- walks
- strikeouts
- home_runs
- innings pitched where relevant
- game_date

### pitcher_games

Suggested columns:

- game_pk
- pitcher_id
- team_id
- started_flag
- innings_pitched
- earned_runs
- hits_allowed
- walks
- strikeouts
- home_runs_allowed
- pitches
- game_date

### odds_snapshots

Suggested columns:

- odds_event_id
- game_pk if matched
- bookmaker
- market
- snapshot_timestamp
- commence_time
- home_team_name
- away_team_name
- home_price
- away_price

## Gold Tables

### training_games
One row per MLB game.

Contains:

- game identifier
- prediction timestamp convention
- home features
- away features
- differential features
- optional market features
- target_home_win

### prediction_games
Same schema as training rows where possible, but without target at prediction time.

## Data Matching Rules

Odds-to-MLB game matching should use:

1. normalized team names/IDs
2. scheduled date/time tolerance
3. home/away orientation
4. doubleheader awareness
5. reschedule/postponement handling

Never rely on team names alone.

## Data Quality Checks

Required checks:

- duplicate `game_pk`
- impossible scores
- missing team IDs
- same home/away team
- game timestamps after result timestamps
- odds snapshots after first pitch when pregame price is expected
- duplicate odds snapshots
- unmatched odds events
- postponed/suspended games
- doubleheaders
- starter changes

## Reproducibility

Transformations should be deterministic. Re-running Silver/Gold creation against the same Bronze data and configuration should produce the same output.
