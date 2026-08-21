"""Pydantic response models for the read-only prediction API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]


class Prediction(BaseModel):
    game_pk: int
    run_date: str | None = None
    first_pitch: str | None = Field(default=None, validation_alias="game_start_pacific")
    home_team: str
    away_team: str
    pick: str
    recommendation: str
    model_probability: float
    market_probability: float
    edge: float
    odds_snapshot_timestamp: str
    odds_snapshot_pacific: str | None = None
    prediction_timestamp: datetime | None = None
    prediction_timestamp_pacific: str | None = None
    model_version: str
    result_status: str
    result_label: str | None = None
    correct: bool | None = None

    model_config = {"populate_by_name": True}


class PredictionListResponse(BaseModel):
    run_date: str
    predictions: list[Prediction]


class PredictionDetailResponse(BaseModel):
    run_date: str
    prediction: Prediction
