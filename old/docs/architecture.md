# Architecture

## Purpose
Define the major systems and boundaries of the MLB moneyline predictor.

## Major Systems

### 1. Data Layer
Responsible for external API access, raw persistence, normalized tables, and storage.

### 2. Point-in-Time Feature Layer
Builds model features using only data available before the prediction timestamp.

### 3. Modeling Layer
Trains probabilistic classifiers and calibrates predicted win probabilities.

### 4. Market Layer
Converts moneylines to probabilities, removes vig, and creates market comparison features/metrics.

### 5. Evaluation Layer
Runs walk-forward experiments, compares training windows/models, and scores model quality.

### 6. Production Prediction Layer
Creates daily predictions, versions outputs, and stores immutable prediction records.

### 7. Presentation Layer
Streamlit application for today's board, model detail, backtest results, and historical journal.

## Data Flow

```text
External APIs
    |
    v
Bronze raw snapshots
    |
    v
Silver normalized tables
    |
    v
Point-in-time feature builder
    |
    v
Gold feature table
    |
    +--> training/evaluation
    |
    +--> daily prediction
```

## Storage Philosophy

Raw responses should be immutable whenever practical. Silver and Gold datasets should be reproducible from upstream sources.

Use:

- JSON for raw API snapshots
- Parquet for analytical datasets
- DuckDB for querying and joins

## System Boundaries

The Streamlit app must not contain core business logic. It should call reusable services/functions from `src/`.

The model training code must not directly fetch APIs. Training should consume deterministic Gold datasets.

The feature layer must not know future game outcomes when constructing a row.

## Prediction Contract

Every prediction should contain at minimum:

- prediction_id
- game_pk
- prediction_timestamp
- model_name
- model_version
- training_window_strategy
- feature_version
- home_win_probability
- away_win_probability
- market_home_probability if available
- market_away_probability if available
- edge if available
- source odds snapshot timestamp

Predictions should be append-only after creation.
