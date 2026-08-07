# Codex Workflow

## Purpose
This document describes how to use Codex to implement the project incrementally without losing architectural discipline.

## General Rule
Codex should read `README.md` and relevant files under `docs/` before implementing each subsystem.

Do not ask Codex to build the entire project in one pass.

## Recommended Implementation Sequence

### Task 1 — Repository Scaffold
Prompt:

> Read README.md and docs/architecture.md. Scaffold the repository structure, Python package, configuration approach, logging, DuckDB connection layer, Bronze/Silver/Gold directories, .env.example, and pytest setup. Do not implement API ingestion or modeling yet. Run tests before finishing.

### Task 2 — MLB Ingestion
Prompt:

> Read docs/data.md and docs/pipeline.md. Implement the MLB ingestion layer for schedules and historical games. Persist raw responses in Bronze, normalize games into Silver, make jobs idempotent, and add tests. Do not implement model features yet.

### Task 3 — Team and Pitcher Normalization
Prompt:

> Extend the Silver layer with team-game and pitcher-game tables according to docs/data.md. Add deterministic transforms and data-quality tests.

### Task 4 — Point-in-Time Features
Prompt:

> Read docs/features.md and docs/testing.md. Implement V1 point-in-time feature generation. Every rolling feature must exclude the current game. Add synthetic leakage tests proving future games cannot alter earlier feature rows.

### Task 5 — Baseline Modeling
Prompt:

> Read docs/modeling.md and docs/evaluation.md. Implement Logistic Regression training and expanding walk-forward evaluation first. Save fold metrics and model metadata. Do not add Random Forest or XGBoost until the baseline pipeline works.

### Task 6 — Model Comparison
Prompt:

> Add Random Forest and XGBoost using the same features and chronological folds. Implement expanding, rolling-2, and rolling-3 window strategies. Produce a comparison table using log loss, Brier score, ROC-AUC, and accuracy.

### Task 7 — Odds Layer
Prompt:

> Implement timestamped odds ingestion and the market engine. Add American-odds conversion, no-vig normalization, odds/game matching, and tests for postponed games and doubleheaders. Keep historical odds availability configurable.

### Task 8 — Calibration
Prompt:

> Add probability calibration for competitive models. Ensure calibration uses only appropriate historical data and never the final holdout. Compare calibrated vs uncalibrated log loss and Brier score.

### Task 9 — Prediction Journal
Prompt:

> Implement an append-only prediction journal according to docs/architecture.md and docs/pipeline.md. Each prediction must store model version, feature version, prediction timestamp, probabilities, market snapshot metadata, and edge.

### Task 10 — Streamlit
Prompt:

> Read docs/app.md. Build a lightweight Streamlit UI that consumes existing service/repository functions. Do not duplicate feature or model logic inside Streamlit.

## Codex Guardrails

Tell Codex explicitly to:

- run tests after changes
- summarize changed files
- avoid broad refactors unrelated to the task
- preserve public interfaces unless necessary
- add migrations/schema changes deliberately
- never use future game outcomes to fill missing pregame features
- keep production predictions immutable

## Suggested Branch Strategy

Optional but useful:

```text
main
feature/data-foundation
feature/mlb-ingestion
feature/features-v1
feature/model-baseline
feature/model-comparison
feature/odds
feature/streamlit
```

## Definition of a Good Codex Task

A task should:

- name the relevant docs
- define one subsystem
- state what not to build yet
- require tests
- define expected outputs

Avoid prompts like:

> Build the MLB predictor.

Prefer prompts like:

> Implement the expanding-window fold generator from docs/evaluation.md. It should accept a DataFrame with season and target columns, yield train/test indices, reject invalid chronology, and include unit tests for the 2021-2025 examples in the documentation.
