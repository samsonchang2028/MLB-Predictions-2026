# API-001 — minimal read-only prediction HTTP adapter

## Status

ready

## Dependencies

- APP-001 / PIPE-002 (artifact-backed `daily.jsonl` + optional `journal.jsonl`)

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes — branch `agent/API-001-read-only-fastapi`

---

## Goal

Add the **smallest** read-only HTTP layer over existing prediction readers. No new business logic, no pipeline changes, no future-facing scaffolding.

**Rejected from v1 (do not design or stub):**

- Auth / API keys
- POST run-triggering (`daily_predictions`, enrich)
- Homepage summary, best-plays, holdout, or other reporting APIs
- Slate listing as its own resource (client uses known `run_date` or `/today`)
- WebSockets, GraphQL, gRPC
- `config.py` / dependency-injection / plugin architecture
- CORS middleware (add only when a real client needs it — separate task)
- `load_game_detail` feature/odds breakdown (separate task if ever needed)

---

## Architecture

```text
HTTP handler  →  Pydantic response  ←  app.board.load_daily_board (existing)
                                      ←  JsonLinesPredictionStore (existing)
                                      ←  JsonLinesJournalStore (existing, optional)
```

One module for routes + schemas. No `routes/` package, no `deps.py`, no `create_app()` factory unless a single `main.py` needs it for tests.

---

## Public contract (4 endpoints only)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | `{ "status": "ok" }` |
| `GET` | `/v1/predictions/today` | Latest `run_date` from store; same rows as dated list |
| `GET` | `/v1/predictions/{run_date}` | List shaped board rows for that slate |
| `GET` | `/v1/predictions/{run_date}/{game_pk}` | Single board row; `404` if missing |

**Data source:** [`load_daily_board`](src/app/board.py) + [`latest_run_date`](src/app/board.py) + existing journal join (same as Streamlit daily board).

**Not in v1:** raw PIPE-001 fields beyond what the board row already exposes; feature breakdown; multi-book odds.

---

## Pydantic schemas (`src/api/schemas.py`)

Mirror the board row fields the API actually returns — no passthrough of the entire `dict`:

- `PredictionSummary`: `game_pk`, `run_date`, `matchup`, `model_probability`, `market_probability`, `edge`, `play`, `model_side`, `action_label`, result fields when journaled (`correct`, etc.), timestamps already on the board row
- `PredictionListResponse`: `run_date`, `predictions: list[PredictionSummary]`
- `HealthResponse`: `status: Literal["ok"]`

Map from `load_daily_board` output in the handler (thin conversion, no recompute).

---

## Implementation sketch

```text
src/api/
  __init__.py
  main.py       # FastAPI app, 4 routes, path env overrides inline
  schemas.py    # Pydantic models

tests/unit/api/
  test_predictions_api.py
```

**`main.py` responsibilities only:**

1. Read store paths from env with same defaults as Streamlit (`PREDICTIONS_STORE_PATH`, `PREDICTION_JOURNAL_PATH`).
2. If `daily.jsonl` missing → `503` with short message.
3. Call `load_daily_board` / `latest_run_date`; filter for detail route.
4. Return Pydantic models.

**Run locally:**

```powershell
pip install -e ".[api]"
python -m uvicorn api.main:app --reload --port 8000
```

(`api` optional extra: `fastapi`, `uvicorn[standard]` only.)

---

## Allowed files

- `src/api/`
- `tests/unit/api/`
- `pyproject.toml` (optional `api` extra only)
- `requirements.txt` (document `.[api]` if needed)
- `tasks/API-001-read-only-fastapi.md`

## Do not modify

- `src/pipelines/`
- `src/models/`
- `src/market/`
- `src/app/board.py` (unless a one-line export is strictly required — prefer zero changes)

---

## Tests (focused)

`tests/unit/api/test_predictions_api.py` using `TestClient`:

1. `GET /health` → 200
2. `GET /v1/predictions/{date}` with tmp_path JSONL fixture → 200, correct row count/shape
3. `GET /v1/predictions/today` → uses latest date in fixture
4. `GET /v1/predictions/{date}/{game_pk}` → 200 for hit, `404` for miss
5. Missing `daily.jsonl` → `503`

Reuse fixture patterns from [`tests/unit/app/test_board.py`](tests/unit/app/test_board.py). No DuckDB, no network.

---

## Acceptance criteria

- Four endpoints only; OpenAPI auto-docs at `/docs`
- Handlers are thin wrappers over `load_daily_board`
- Public JSON shape is Pydantic-defined and tested
- No changes to pipeline / model / market code
- Full existing test suite still passes

---

## Estimated size

~150–250 lines total (main + schemas + tests). Not 400–600.

---

## Check-in before dispatch

**Approved** — user confirmed orchestrator dispatch (slim scope: board rows only, no CORS v1).
