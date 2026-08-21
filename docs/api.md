# Read-only HTTP API (v1)

Minimal FastAPI adapter over [`app.board.load_daily_board`](../src/app/board.py). Same artifact-backed board rows as Streamlit — no recompute at the HTTP layer.

**Task spec:** [`tasks/API-001-read-only-fastapi.md`](../tasks/API-001-read-only-fastapi.md)

---

## Prerequisites

1. Run the daily operator:

   ```powershell
   python scripts\daily_predictions.py --date YYYY-MM-DD
   ```

2. Install the API extra:

   ```powershell
   pip install -e ".[api]"
   ```

---

## Run locally

```powershell
python -m uvicorn api.main:app --reload --port 8000
```

| URL | Purpose |
|-----|---------|
| http://127.0.0.1:8000/docs | Swagger UI |
| http://127.0.0.1:8000/openapi.json | OpenAPI schema |

Or: `python scripts\run_api.py`

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/v1/predictions/today` | Latest slate board rows |
| `GET` | `/v1/predictions?date=YYYY-MM-DD` | Board rows for one slate |
| `GET` | `/v1/predictions/{game_pk}` | Single game; optional `?date=` (defaults to latest) |

### Response fields (per prediction)

`game_pk`, `run_date`, `first_pitch`, `home_team`, `away_team`, `pick`, `recommendation`, `model_probability`, `market_probability`, `edge`, `odds_snapshot_timestamp`, `prediction_timestamp`, `model_version`, `result_status`, `result_label`, `correct` (when journaled).

List responses: `{ "run_date": "...", "predictions": [...] }`.  
Detail responses: `{ "run_date": "...", "prediction": { ... } }`.

---

## Errors

| Condition | Status |
|-----------|--------|
| `daily.jsonl` missing | `503` |
| Unknown `date` | `404` |
| Unknown `game_pk` for slate | `404` |

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PREDICTIONS_STORE_PATH` | `state/predictions/daily.jsonl` | Prediction store |
| `PREDICTION_JOURNAL_PATH` | `state/predictions/journal.jsonl` | Post-game results (optional) |
| `CORS_ORIGINS` | unset (CORS off) | Comma-separated browser origins; set explicitly for a frontend |

Example:

```powershell
$env:CORS_ORIGINS = "http://localhost:3000"
python -m uvicorn api.main:app --port 8000
```

---

## Example requests

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/predictions/today
curl "http://127.0.0.1:8000/v1/predictions?date=2026-08-14"
curl http://127.0.0.1:8000/v1/predictions/746789
curl "http://127.0.0.1:8000/v1/predictions/746789?date=2026-08-14"
```

---

## Out of scope (v1)

Auth, POST run triggers, slate listing, homepage summary, best plays, game feature breakdown, DuckDB, Monte Carlo.
