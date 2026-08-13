# APP-004 — Streamlit deployment packaging

## Status

Completed

## Dependencies

- APP-001
- APP-002
- APP-003

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `no`

## Goal

Make the existing Streamlit daily-board and performance dashboards reproducible
on Streamlit Community Cloud without requiring local DuckDB data.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/app/daily_board_page.py`
- `src/app/performance_page.py`
- `src/app/board.py`
- `src/app/performance.py`

## Allowed files

- `requirements.txt`
- `.streamlit/config.toml`
- `streamlit_app.py`
- `pages/`
- `tasks/APP-004-streamlit-deployment.md`

## May modify if necessary

- deployment-only documentation

## Do not modify

- model, feature, ingestion, market, or pipeline logic
- `state/CURRENT.md`
- `tasks/index.md`

## Inputs

- Existing Streamlit pages under `src/app/`
- Committed/report-backed artifacts:
  - `reports/experiments/v1-repaired-a910017bac839af5.json`
  - `reports/experiments/v1-holdout-2026.json`
  - optional `state/predictions/daily.jsonl`
  - optional `state/predictions/journal.jsonl`

## Outputs

- Root Streamlit Cloud app entrypoint
- Multipage wrappers for both existing dashboards
- Streamlit config
- Cloud-installable Python requirements

## Requirements

- The Streamlit Cloud main file is `streamlit_app.py`.
- Both existing dashboards are reachable from the deployed app sidebar.
- Python dependencies are installable by Streamlit Cloud from the repository
  root.
- The deployment contract is explicit: `data/mlb.duckdb` and raw/local data
  builds are not deployed artifacts. The app reads committed or otherwise
  provisioned JSON/report artifacts unless later operator automation publishes
  fresh ones.
- Existing dashboard/page logic remains unchanged.

## Critical correctness constraints

- Do not add model, market, feature, or metric computation to the UI layer.
- Do not require a local DuckDB database for the deployed app to render its
  artifact-backed pages.
- Do not change prediction, holdout, or evaluation methodology.

## Acceptance criteria

- `requirements.txt` installs the package with the Streamlit app extra.
- `.streamlit/config.toml` exists with non-interactive cloud-safe defaults.
- `streamlit_app.py` renders a landing page documenting the artifact-backed
  deployment contract.
- `pages/1_Daily_Predictions.py` runs `src/app/daily_board_page.py`.
- `pages/2_Model_Performance.py` runs `src/app/performance_page.py`.
- Focused syntax/import validation passes for the new deployment files.

## Required tests

- Focused syntax/import validation for deployment wrappers and app entrypoint.

## Handoff

- Summary: Added Streamlit Cloud packaging around the existing app pages without
  changing dashboard logic.
- Files changed:
  - `requirements.txt`
  - `.streamlit/config.toml`
  - `streamlit_app.py`
  - `pages/1_Daily_Predictions.py`
  - `pages/2_Model_Performance.py`
  - `tasks/APP-004-streamlit-deployment.md`
- Commands run:
  - `python -m py_compile streamlit_app.py pages/1_Daily_Predictions.py pages/2_Model_Performance.py`
  - `python -m pip show streamlit`
  - `python streamlit_app.py`
  - `python pages/1_Daily_Predictions.py`
  - `python pages/2_Model_Performance.py`
- Test results:
  - PASS: deployment entrypoint and page wrappers compile.
  - PASS: local environment has Streamlit installed (`1.61.1`).
  - PASS: entrypoint and wrappers execute as plain Python smoke checks.
    Streamlit emits expected bare-mode `missing ScriptRunContext` warnings when
    not launched with `streamlit run`.
- Known limitations:
  - Streamlit Community Cloud will only show artifacts committed to the repo or
    otherwise provisioned to the runtime. `data/mlb.duckdb`,
    `mlb_odds_dataset.json`, and `.env` remain local/ignored inputs.
  - Fresh daily predictions still require a separate operator or scheduled
    automation task to generate/publish `state/predictions/daily.jsonl`.
- ADR/state changes:
  - No ADR needed.
  - `state/CURRENT.md` should be updated by the orchestrator after review/test
    gates pass.
