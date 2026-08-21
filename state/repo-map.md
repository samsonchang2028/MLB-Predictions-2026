# Repository Map

Compact index of where to start. Not a substitute for source code. Update when structure materially changes.

## Root entrypoints

- `AGENTS.md` — agent workflow, ML/data rules, read order
- `state/CURRENT.md` — milestone, completed tasks, blockers
- `state/repo-map.md` — this file
- `state/task-context/` — per-task context packs for downstream agents
- `state/agents/<TASK-ID>.md` — active task observability (orchestrator)
- `tasks/` — task definitions (`tasks/index.md`, `tasks/TASK_TEMPLATE.md`)
- `streamlit_app.py` — Streamlit app entry
- `scripts/` — operator and one-off scripts (daily predictions, holdout, diagnostics)

## Data — ingestion (`src/ingestion/`)

- `mlb/schedule.py` — MLB schedule ingestion
- `mlb/game_detail.py` — game detail backfill
- `mlb/statsapi_fetchers.py` — StatsAPI fetch adapters
- `odds/snapshots.py` — live moneyline odds snapshots
- `odds/historical.py` — historical odds archive

## Data — storage & transforms

- `src/storage/foundation.py` — DuckDB/Parquet foundation
- `src/transforms/silver.py` — Silver normalization

## Features (`src/features/`)

- `team.py` — point-in-time team strength / form
- `starter.py` — starting-pitcher features (shift-before-roll)
- `bullpen.py` — bullpen workload and rates
- `build.py` — game feature matrix (one row per `game_pk`)
- `completeness.py` — feature coverage guards

## Models (`src/models/`)

- `logistic.py`, `random_forest.py`, `xgboost_model.py` — P(home_win) families
- Shared `build_model` / `predict_proba` / `model_metadata` contract

## Evaluation (`src/evaluation/`)

- `splits.py` — chronological walk-forward folds (2026 excluded)
- `runner.py` — per-fold evaluation and optional predictions
- `calibration.py`, `holdout.py` — calibration and final holdout

## Experiments (`src/experiments/`)

- `expanding.py`, `rolling.py`, `comparison.py` — training-window experiments

## Market (`src/market/`)

- `engine.py` — odds conversion, no-vig, edge, EV

## Simulation (`src/simulation/`)

- `game_level.py` — game-level simulation
- `score_model.py` — score/totals modeling

## Pipelines (`src/pipelines/`)

- `daily.py` — daily prediction pipeline
- `certify_historical.py` — build → certify historical dataset

## Validation (`src/validation/`)

- `runner.py`, `checks.py`, `results.py` — dataset validation
- `certification.py` — PASS/FAIL certification artifacts (`state/data-certifications/`)
- `odds_mapping.py` — odds → `game_pk` mapping audit
- `leakage.py`, `coverage.py` — leakage and coverage checks

## App (`src/app/`)

- `homepage.py`, `board.py`, `daily_board_page.py` — main UI surfaces
- `game_detail.py`, `game_detail_page.py` — game detail
- `performance.py`, `performance_page.py` — model performance
- `best_plays.py`, `about.py` — picks and methodology

## Observability (`src/observability/`)

- `journal.py` — prediction journal (`state/predictions/`)

## Tests

- `tests/unit/` — module-level tests mirroring `src/` layout
- `tests/integration/` — cross-module ingestion, pipelines, validation
- `tests/leakage/` — temporal leakage and future-mutation tests

Key leakage tests: `test_starter_leakage.py`, `test_bullpen_leakage.py`, `test_feature_matrix.py`, `test_split_leakage.py`, `test_calibration_leakage.py`

## Architecture & docs

- `docs/decisions/` — ADRs (ADR-001 storage through ADR-006 methodology lock)
- `docs/` — project documentation and roadmap

## Tasks

- `tasks/<TASK-ID>-<slug>.md` — scoped work units with allowed files and acceptance criteria
- Prefix families: `DATA-`, `FEAT-`, `ML-`, `MARKET-`, `APP-`, `PIPE-`, `SIM-`, `OBS-`, `OPS-`, `META-`, `DOCS-`
