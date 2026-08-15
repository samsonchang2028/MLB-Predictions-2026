"""Minimal read-only FastAPI surface over :func:`app.board.load_daily_board`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from app.board import load_daily_board, latest_run_date
from observability.journal import JsonLinesJournalStore
from pipelines.daily import JsonLinesPredictionStore

from api.schemas import (
    HealthResponse,
    PredictionDetailResponse,
    PredictionListResponse,
    PredictionSummary,
)

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")

app = FastAPI(title="MLB Moneyline Predictions API")


def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


def _require_store_path() -> Path:
    path = _store_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail="Prediction store not available")
    return path


def _stores() -> tuple[JsonLinesPredictionStore, JsonLinesJournalStore | None]:
    store = JsonLinesPredictionStore(_require_store_path())
    journal_path = _journal_path()
    journal_store = JsonLinesJournalStore(journal_path) if journal_path.exists() else None
    return store, journal_store


def _to_summary(row: dict[str, Any]) -> PredictionSummary:
    return PredictionSummary.model_validate(row)


def _list_response(run_date: str, rows: list[dict[str, Any]]) -> PredictionListResponse:
    return PredictionListResponse(
        run_date=run_date,
        predictions=[_to_summary(row) for row in rows],
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/v1/predictions/today", response_model=PredictionListResponse)
def predictions_today() -> PredictionListResponse:
    store, journal_store = _stores()
    run_date = latest_run_date(store)
    if run_date is None:
        raise HTTPException(status_code=503, detail="No predictions in store")
    rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
    return _list_response(run_date, rows)


@app.get("/v1/predictions/{run_date}", response_model=PredictionListResponse)
def predictions_for_date(run_date: str) -> PredictionListResponse:
    store, journal_store = _stores()
    rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
    return _list_response(run_date, rows)


@app.get(
    "/v1/predictions/{run_date}/{game_pk}",
    response_model=PredictionDetailResponse,
)
def prediction_detail(run_date: str, game_pk: int) -> PredictionDetailResponse:
    store, journal_store = _stores()
    rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
    for row in rows:
        if row["game_pk"] == game_pk:
            return PredictionDetailResponse(
                run_date=run_date,
                prediction=_to_summary(row),
            )
    raise HTTPException(status_code=404, detail="Prediction not found")
