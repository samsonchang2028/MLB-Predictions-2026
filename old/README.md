# MLB Moneyline Predictor

## Project Goal
Build an end-to-end MLB moneyline forecasting platform that ingests baseball and market data, creates point-in-time-safe features, trains and compares probabilistic classifiers, evaluates them with walk-forward validation, and serves daily predictions in a lightweight Streamlit app.

The project is primarily an ML/data-engineering experiment. Any betting-style output is treated as simulation or a side challenge. The model should be judged on predictive quality, calibration, and performance relative to the market—not on short-term bankroll outcomes.

## V1 Scope

V1 should include:

- MLB historical and daily data ingestion
- Odds ingestion for current/future forward testing
- Raw/normalized/model-ready data layers
- DuckDB + Parquet storage
- Point-in-time-safe feature engineering
- Logistic Regression baseline
- Random Forest comparison model
- XGBoost primary advanced model
- Expanding-window validation
- Rolling 2-season validation
- Rolling 3-season validation
- Probability calibration
- Market implied probability and vig removal
- Walk-forward backtesting
- Daily prediction pipeline
- Immutable prediction journal
- Streamlit dashboard
- Tests for leakage, transformations, and backtest logic

## Explicitly Not V1

- Automated wagering
- Neural networks
- Weather modeling
- Batter-vs-pitcher modeling
- Complex confirmed-lineup modeling
- Kafka/Spark
- Kubernetes
- Multi-cloud infrastructure
- Kelly sizing
- Large-scale real-time architecture

## Core Architecture

```text
MLB Stats API       Odds API
      |                 |
      +--------+--------+
               |
          Raw ingestion
               |
          Bronze storage
               |
         Transform/clean
               |
          Silver tables
               |
       Point-in-time features
               |
           Gold dataset
               |
      +--------+--------+
      |        |        |
 Logistic      RF     XGBoost
      |        |        |
      +--------+--------+
               |
         Model comparison
               |
          Calibration
               |
         P(home_team_win)
               |
      Market no-vig probability
               |
           Edge / EV
               |
        Prediction journal
               |
          Streamlit app
```

## Primary Research Questions

1. Which model family performs best on MLB moneyline win probability prediction?
2. Does an expanding historical training window outperform a recent rolling window?
3. Does the model improve on the sportsbook market baseline?
4. Are predicted probabilities well calibrated?
5. Do apparent betting edges persist out of sample?

## Model Families

V1 trains three separate classifiers on the same feature sets and validation folds:

- Logistic Regression — simple/interpretable baseline
- Random Forest — nonlinear tree ensemble comparison
- XGBoost — primary advanced tabular model candidate

An ensemble or stacking model should only be added if the standalone comparison demonstrates that it improves out-of-sample performance.

## Validation Strategy

Primary expanding walk-forward:

```text
Train 2021       -> Test 2022
Train 2021-2022  -> Test 2023
Train 2021-2023  -> Test 2024
Train 2021-2024  -> Test 2025
```

Recent-window experiments:

```text
Rolling 2-season
Train 2021-2022 -> Test 2023
Train 2022-2023 -> Test 2024
Train 2023-2024 -> Test 2025

Rolling 3-season
Train 2021-2023 -> Test 2024
Train 2022-2024 -> Test 2025
```

Final holdout:

```text
Train using the selected methodology on 2021-2025
Test on 2026 YTD
```

2026 should remain untouched until model family, lookback strategy, feature set, tuning process, and calibration approach are selected.

## Primary Metrics

Rank models primarily with:

1. Log loss
2. Brier score
3. Calibration quality
4. ROC-AUC
5. Performance vs market baseline

Secondary metrics:

- Accuracy
- Simulated ROI
- Units won/lost
- Maximum drawdown
- Bet count by edge bucket
- Closing-line value if historical/current data supports it

## Critical Engineering Rule

Every feature must be reproducible using only information that was available before the prediction timestamp.

No future leakage is allowed.

Examples:

- Rolling stats must shift before rolling
- The current game's result must not enter its own features
- Odds must use a timestamped pregame snapshot
- Starting pitcher information must reflect what was known at prediction time
- Postponed/rescheduled games must not be silently merged incorrectly

## Proposed Repository Structure

```text
mlb-moneyline-predictor/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── features.yaml
│   ├── model.yaml
│   └── pipeline.yaml
├── data/
│   ├── bronze/
│   ├── silver/
│   └── gold/
├── docs/
│   ├── architecture.md
│   ├── data.md
│   ├── features.md
│   ├── modeling.md
│   ├── evaluation.md
│   ├── pipeline.md
│   ├── app.md
│   ├── testing.md
│   └── codex_workflow.md
├── src/
│   ├── ingestion/
│   ├── transforms/
│   ├── features/
│   ├── models/
│   ├── backtest/
│   ├── pipelines/
│   ├── storage/
│   └── utils/
├── tests/
├── notebooks/
├── models/
└── streamlit_app/
```

## Suggested Technology Stack

- Python
- DuckDB
- Parquet
- pandas or Polars
- scikit-learn
- XGBoost
- Streamlit
- pytest
- pydantic/settings or equivalent configuration layer
- Docker later in V1
- MLflow optional, not required initially

## Build Order

1. Repository and configuration scaffold
2. DuckDB schema + Bronze/Silver/Gold layout
3. MLB schedule/game backfill
4. Team-game and pitcher-game normalization
5. Point-in-time feature builder
6. Logistic Regression baseline
7. Walk-forward evaluation framework
8. Random Forest and XGBoost experiments
9. Odds ingestion and no-vig market baseline
10. Calibration
11. Prediction journal
12. Daily prediction pipeline
13. Streamlit dashboard
14. Automation and observability

## Definition of V1 Done

V1 is done when the repository can:

1. Rebuild historical model-ready data from stored raw data.
2. Train all three model families under expanding and rolling-window schemes.
3. Produce repeatable evaluation reports.
4. Score an unseen game using only pregame data.
5. Save that prediction immutably with a timestamp and model version.
6. Show current predictions and historical performance in Streamlit.
7. Pass leakage and transformation tests.
