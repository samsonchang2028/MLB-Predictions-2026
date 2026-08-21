"""Minimal read-only FastAPI adapter over :func:`app.board.load_daily_board`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.board import available_run_dates, latest_run_date, load_daily_board
from observability.journal import JsonLinesJournalStore
from pipelines.daily import JsonLinesPredictionStore

from api.schemas import (
    HealthResponse,
    Prediction,
    PredictionDetailResponse,
    PredictionListResponse,
)

DEFAULT_STORE_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")


def _store_path() -> Path:
    return Path(os.environ.get("PREDICTIONS_STORE_PATH", DEFAULT_STORE_PATH))


def _journal_path() -> Path:
    return Path(os.environ.get("PREDICTION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))


def _cors_origins() -> list[str] | None:
    raw = os.environ.get("CORS_ORIGINS")
    if raw is None or not raw.strip():
        return None
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    return origins or None


def _require_store_path() -> Path:
    path = _store_path()
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="Prediction store not available; run daily_predictions first",
        )
    return path


def _stores() -> tuple[JsonLinesPredictionStore, JsonLinesJournalStore | None]:
    store = JsonLinesPredictionStore(_require_store_path())
    journal_path = _journal_path()
    journal_store = JsonLinesJournalStore(journal_path) if journal_path.exists() else None
    return store, journal_store


def _to_prediction(row: dict[str, Any]) -> Prediction:
    return Prediction.model_validate(row)


def _list_response(run_date: str, rows: list[dict[str, Any]]) -> PredictionListResponse:
    return PredictionListResponse(
        run_date=run_date,
        predictions=[_to_prediction(row) for row in rows],
    )


def _resolve_run_date(store: JsonLinesPredictionStore, run_date: str | None) -> str:
    if run_date is not None:
        if run_date not in available_run_dates(store):
            raise HTTPException(status_code=404, detail=f"No predictions for date: {run_date}")
        return run_date
    resolved = latest_run_date(store)
    if resolved is None:
        raise HTTPException(status_code=503, detail="No predictions in store")
    return resolved


def create_app() -> FastAPI:
    application = FastAPI(title="MLB Moneyline Predictions API", version="1.0.0")
    origins = _cors_origins()
    if origins is not None:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/v1/predictions/today", response_model=PredictionListResponse)
    def predictions_today() -> PredictionListResponse:
        store, journal_store = _stores()
        run_date = _resolve_run_date(store, None)
        rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
        return _list_response(run_date, rows)

    @application.get("/v1/predictions", response_model=PredictionListResponse)
    def predictions_for_date(
        date: str = Query(..., description="Slate run_date (YYYY-MM-DD)"),
    ) -> PredictionListResponse:
        store, journal_store = _stores()
        run_date = _resolve_run_date(store, date)
        rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
        return _list_response(run_date, rows)

    @application.get("/v1/predictions/{game_pk}", response_model=PredictionDetailResponse)
    def prediction_detail(
        game_pk: int,
        date: str | None = Query(
            None,
            description="Slate run_date; defaults to latest in store",
        ),
    ) -> PredictionDetailResponse:
        store, journal_store = _stores()
        run_date = _resolve_run_date(store, date)
        rows = load_daily_board(store, run_date=run_date, journal_store=journal_store)
        for row in rows:
            if row["game_pk"] == game_pk:
                return PredictionDetailResponse(
                    run_date=run_date,
                    prediction=_to_prediction(row),
                )
        raise HTTPException(status_code=404, detail="Prediction not found")

    return application


app = create_app()
