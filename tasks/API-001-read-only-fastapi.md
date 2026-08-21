# API-001 — minimal read-only prediction HTTP adapter

## Status

complete

## Dependencies

- APP-001 / PIPE-002 (artifact-backed `daily.jsonl` + optional `journal.jsonl`)

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

---

## Goal

Add the **smallest** read-only HTTP layer over existing prediction readers. No new business logic, no pipeline changes.

**Rejected from v1 (do not design or stub):**

- Auth / API keys
- POST run-triggering (`daily_predictions`, enrich)
- Homepage summary, best-plays, slate listing, game feature breakdown
- WebSockets, GraphQL, gRPC
- `config.py` / dependency-injection / `routes/` package
- Expanded endpoint surface beyond the four prediction routes below

---

## Architecture

```text
HTTP handler  →  Pydantic response  ←  app.board.load_daily_board (existing)
                                      ←  JsonLinesPredictionStore (existing)
                                      ←  JsonLinesJournalStore (existing, optional)
```

---

## Public contract (4 endpoints)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | `{ "status": "ok" }` |
| `GET` | `/v1/predictions/today` | Latest `run_date` from store |
| `GET` | `/v1/predictions?date=YYYY-MM-DD` | Board rows for that slate |
| `GET` | `/v1/predictions/{game_pk}` | Single board row; optional `?date=` (defaults to latest); `404` if missing |

**Data source:** [`load_daily_board`](src/app/board.py) + [`latest_run_date`](src/app/board.py) / [`available_run_dates`](src/app/board.py).

Missing `daily.jsonl` → `503`. Unknown date → `404`.

---

## Module layout

```text
src/api/
  __init__.py
  main.py
  schemas.py

scripts/run_api.py
tests/unit/api/test_predictions_api.py
```

**CORS:** enabled only when `CORS_ORIGINS` is set (comma-separated). No permissive default.

---

## Run locally

```powershell
pip install -e ".[api]"
python -m uvicorn api.main:app --reload --port 8000
```

---

## Allowed files

- `src/api/`
- `tests/unit/api/`
- `pyproject.toml` (optional `api` extra only)
- `requirements.txt`
- `README.md`, `docs/api.md`, `.github/README.md`
- `scripts/run_api.py`
- `tasks/API-001-read-only-fastapi.md`

## Do not modify

- `src/pipelines/`
- `src/models/`
- `src/market/`
- `src/app/board.py` (unless strictly required — prefer zero changes)

---

## Acceptance criteria

- Four prediction endpoints + `/health`; OpenAPI at `/docs`
- Handlers are thin wrappers over `load_daily_board`
- Pydantic-defined JSON contract and unit tests
- CORS only when `CORS_ORIGINS` is configured
- No pipeline / model / market changes
