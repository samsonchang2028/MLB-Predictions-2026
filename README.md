# MLB Moneyline Predictor

A local-first MLB moneyline prediction system with certified data ingestion,
point-in-time feature engineering, walk-forward model evaluation, market edge
calculation, and Streamlit dashboards.

## Current V1 status

V1 historical/model work is complete. The repaired 2021-2025 dataset certified
PASS after the MLB game-detail pitching-stat projection defect was fixed and the
historical data was re-ingested. Gold feature completeness and leakage checks
passed before model selection.

The locked V1 methodology is documented in
`docs/decisions/ADR-006-v1-methodology-lock.md`:

- tuned shallow XGBoost,
- expanding training window,
- uncalibrated probabilities,
- selected on repaired certified 2021-2025 data without inspecting 2026.

The final 2026 holdout was evaluated once under that locked methodology. The
local daily operator can now fetch live odds from The Odds API, build same-day
inference features, and write immutable prediction records for the Streamlit
daily board.

## Key artifacts

- Current project state: `state/CURRENT.md`
- Task graph: `tasks/index.md`
- V1 methodology lock: `docs/decisions/ADR-006-v1-methodology-lock.md`
- Repaired 2021-2025 certification: `state/data-certifications/certification-PASS-a910017bac839af5.json`
- 2021-2026 certification / final holdout data: `state/data-certifications/certification-PASS-db7dbc8b8a1c5ae9.json`
- Final 2026 holdout report: `reports/experiments/v1-holdout-2026.json`
- Gold completeness report: `reports/data-quality/gold-completeness-a910017bac839af5.json`

## Run daily predictions locally

Create a local `.env` file in the repo root. It is ignored by git:

```env
THE_ODDS_API_KEY=paste_your_key_here
```

Load it and run the operator:

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
  }
}

python.exe scripts\daily_predictions.py --date 2026-08-13
```

Predictions are appended to:

```text
state/predictions/daily.jsonl
```

## Run Streamlit dashboards

Daily prediction board:

```powershell
python.exe -m streamlit run src\app\daily_board_page.py
```

Model performance dashboard:

```powershell
python.exe -m streamlit run src\app\performance_page.py
```

The daily board displays timestamps in Pacific time and shows the model-preferred
side relative to the no-vig market probability. `PLAY/PASS` is a synthetic
UI threshold only, not a staking policy.

## Data and deployment note

`data/` and `*.duckdb` are ignored because the local DuckDB build is large and
reproducible from ingestion scripts. A deployed Streamlit app should either read
committed lightweight artifacts (reports / prediction JSONL) or use a separate
scheduled operator/data-artifact strategy. Do not commit API keys or local `.env`.
