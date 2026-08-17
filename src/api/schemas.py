"""Pydantic response models for the read-only prediction API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]


class PredictionSummary(BaseModel):
    game_pk: int
    run_date: str | None = None
    matchup: str
    model_probability: float
    market_probability: float
    edge: float
    play: bool
    model_side: str
    action_label: str
    result_status: str
    result_label: str | None = None
    actual_home_win: bool | None = None
    correct: bool | None = None
    game_start_pacific: str | None = None
    prediction_timestamp: datetime | None = None
    prediction_timestamp_pacific: str | None = None
    odds_snapshot_timestamp: str
    odds_snapshot_pacific: str | None = None


class PredictionListResponse(BaseModel):
    run_date: str
    predictions: list[PredictionSummary]


class PredictionDetailResponse(BaseModel):
    run_date: str
    prediction: PredictionSummary
