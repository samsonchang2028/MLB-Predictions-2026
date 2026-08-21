# Repository guide

Documentation for contributors and operators browsing this repo on GitHub.

## Quick links

| Doc | What it covers |
|-----|----------------|
| [Project README](../README.md) | Overview, model stats, quick start |
| [HTTP API reference](../docs/api.md) | FastAPI read-only endpoints, config, examples |
| [Project state](../state/CURRENT.md) | Current status and active work |
| [Task index](../tasks/index.md) | Agent / contributor task graph |
| [ADRs](../docs/decisions/) | Architecture decision records |
| [Agent workflow](../AGENTS.md) | Multi-agent coding rules |

## Surfaces

| Surface | Command | Audience |
|---------|---------|----------|
| **Streamlit dashboard** | `python -m streamlit run streamlit_app.py` | Day-to-day slate review |
| **Read-only HTTP API** | `python -m uvicorn api.main:app --reload --port 8000` | Scripts, future web/mobile clients |
| **Daily operator** | `python scripts/daily_predictions.py --date YYYY-MM-DD` | Writes `state/predictions/*.jsonl` |

Both the dashboard and API read the **same local artifacts**. Run the daily operator before expecting data in either UI.

## HTTP API (summary)

- **Install:** `pip install -e ".[api]"` (included in root `requirements.txt` as `-e .[app,api]`)
- **Docs UI:** http://127.0.0.1:8000/docs after starting the server
- **Full reference:** [docs/api.md](../docs/api.md)

Endpoints (all `GET`):

- `/health`
- `/v1/predictions/today`
- `/v1/predictions?date=YYYY-MM-DD`
- `/v1/predictions/{game_pk}` (optional `?date=`)

Set `CORS_ORIGINS` only when a browser client needs cross-origin access.

## Development

```powershell
# Editable install with app + API + test extras
pip install -e ".[app,api,test]"

# Unit tests (includes API)
python -m pytest tests/unit/api/ tests/unit/app/ -q
```

## Secrets and local data

- `THE_ODDS_API_KEY` — required for live odds in `daily_predictions.py` (set in `.env`, gitignored)
- `data/mlb.duckdb` — local DuckDB build (~1 GB, gitignored)
- `state/predictions/` — daily JSONL outputs (gitignored)

Do not commit API keys, `.env`, or prediction artifacts.

## Contributing

1. Read `AGENTS.md` and `state/CURRENT.md` before large changes.
2. Pick or create a task under `tasks/` for non-trivial work.
3. Preserve point-in-time ML rules (no leakage, chronological splits, 2026 holdout untouched).
4. Add or update tests for behavior changes.
