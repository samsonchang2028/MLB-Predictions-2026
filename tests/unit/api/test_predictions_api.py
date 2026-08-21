"""Unit tests for API-001 read-only prediction HTTP adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.schemas import PredictionDetailResponse, PredictionListResponse


def _record(
    game_pk: int,
    *,
    home_id: int = 147,
    away_id: int = 111,
    edge: float = 0.05,
    run_date: str = "2024-04-01",
) -> dict:
    return {
        "game_pk": game_pk,
        "model_probability": 0.55,
        "market_probability": 0.55 - edge,
        "edge": edge,
        "odds_snapshot_timestamp": "2024-04-01T14:00:00+00:00",
        "prediction_timestamp": "2024-04-01T15:00:00+00:00",
        "game_start_timestamp": "2024-04-02T02:10:00+00:00",
        "model_version": "v1",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "run_date": run_date,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    return TestClient(create_app())


@pytest.fixture
def env_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    daily = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("PREDICTIONS_STORE_PATH", str(daily))
    monkeypatch.setenv("PREDICTION_JOURNAL_PATH", str(journal))
    return daily, journal


def test_health_returns_ok(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_predictions_for_date(env_paths: tuple[Path, Path], client: TestClient):
    daily, _ = env_paths
    _write_jsonl(
        daily,
        [
            _record(1, run_date="2024-04-01"),
            _record(2, run_date="2024-04-01"),
            _record(3, run_date="2024-04-02"),
        ],
    )

    response = client.get("/v1/predictions", params={"date": "2024-04-01"})

    assert response.status_code == 200
    body = PredictionListResponse.model_validate(response.json())
    assert body.run_date == "2024-04-01"
    assert len(body.predictions) == 2
    prediction = body.predictions[0]
    assert prediction.game_pk == 1
    assert prediction.home_team == "NYY"
    assert prediction.away_team == "BOS"
    assert prediction.pick == "NYY"
    assert prediction.model_probability == 0.55
    assert prediction.edge == 0.05
    assert prediction.recommendation == "PLAY NYY"
    assert prediction.model_version == "v1"


def test_today_uses_latest_run_date(env_paths: tuple[Path, Path], client: TestClient):
    daily, _ = env_paths
    _write_jsonl(
        daily,
        [
            _record(1, run_date="2026-08-12"),
            _record(2, run_date="2026-08-13"),
        ],
    )

    response = client.get("/v1/predictions/today")

    assert response.status_code == 200
    body = PredictionListResponse.model_validate(response.json())
    assert body.run_date == "2026-08-13"
    assert [row.game_pk for row in body.predictions] == [2]


def test_unknown_date_returns_404(env_paths: tuple[Path, Path], client: TestClient):
    daily, _ = env_paths
    _write_jsonl(daily, [_record(1, run_date="2024-04-01")])

    response = client.get("/v1/predictions", params={"date": "2099-01-01"})

    assert response.status_code == 404


def test_prediction_detail_hit_and_miss(env_paths: tuple[Path, Path], client: TestClient):
    daily, _ = env_paths
    _write_jsonl(daily, [_record(42, run_date="2024-04-01")])

    hit = client.get("/v1/predictions/42", params={"date": "2024-04-01"})
    miss = client.get("/v1/predictions/99", params={"date": "2024-04-01"})

    assert hit.status_code == 200
    detail = PredictionDetailResponse.model_validate(hit.json())
    assert detail.run_date == "2024-04-01"
    assert detail.prediction.game_pk == 42
    assert detail.prediction.pick == "NYY"
    assert miss.status_code == 404


def test_prediction_detail_defaults_to_latest_date(env_paths: tuple[Path, Path], client: TestClient):
    daily, _ = env_paths
    _write_jsonl(
        daily,
        [
            _record(10, run_date="2026-08-12"),
            _record(20, run_date="2026-08-13"),
        ],
    )

    response = client.get("/v1/predictions/20")

    assert response.status_code == 200
    assert PredictionDetailResponse.model_validate(response.json()).prediction.game_pk == 20


def test_missing_daily_jsonl_returns_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setenv("PREDICTIONS_STORE_PATH", str(missing))
    monkeypatch.delenv("PREDICTION_JOURNAL_PATH", raising=False)
    client = TestClient(create_app())

    response = client.get("/v1/predictions/today")

    assert response.status_code == 503
    assert "Prediction store not available" in response.json()["detail"]


def test_cors_headers_when_origin_allowed(client: TestClient):
    response = client.get(
        "/health",
        headers={"Origin": "https://example.com"},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"


def test_cors_disabled_when_origins_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/health",
        headers={"Origin": "https://example.com"},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") is None
