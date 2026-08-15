"""Unit tests for API-001 read-only prediction HTTP adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app


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
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def env_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    daily = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("PREDICTIONS_STORE_PATH", str(daily))
    monkeypatch.setenv("PREDICTION_JOURNAL_PATH", str(journal))
    return daily, journal


def test_health_returns_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_predictions_for_date(env_paths: tuple[Path, Path]):
    daily, _ = env_paths
    _write_jsonl(
        daily,
        [
            _record(1, run_date="2024-04-01"),
            _record(2, run_date="2024-04-01"),
            _record(3, run_date="2024-04-02"),
        ],
    )

    response = TestClient(app).get("/v1/predictions/2024-04-01")

    assert response.status_code == 200
    body = response.json()
    assert body["run_date"] == "2024-04-01"
    assert len(body["predictions"]) == 2
    prediction = body["predictions"][0]
    assert prediction["game_pk"] == 1
    assert prediction["matchup"] == "BOS @ NYY"
    assert prediction["model_probability"] == 0.55
    assert prediction["edge"] == 0.05
    assert prediction["play"] is True
    assert prediction["action_label"] == "PLAY NYY"


def test_today_uses_latest_run_date(env_paths: tuple[Path, Path]):
    daily, _ = env_paths
    _write_jsonl(
        daily,
        [
            _record(1, run_date="2026-08-12"),
            _record(2, run_date="2026-08-13"),
        ],
    )

    response = TestClient(app).get("/v1/predictions/today")

    assert response.status_code == 200
    body = response.json()
    assert body["run_date"] == "2026-08-13"
    assert [row["game_pk"] for row in body["predictions"]] == [2]


def test_prediction_detail_hit_and_miss(env_paths: tuple[Path, Path]):
    daily, _ = env_paths
    _write_jsonl(daily, [_record(42, run_date="2024-04-01")])

    client = TestClient(app)
    hit = client.get("/v1/predictions/2024-04-01/42")
    miss = client.get("/v1/predictions/2024-04-01/99")

    assert hit.status_code == 200
    assert hit.json()["run_date"] == "2024-04-01"
    assert hit.json()["prediction"]["game_pk"] == 42
    assert miss.status_code == 404


def test_missing_daily_jsonl_returns_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setenv("PREDICTIONS_STORE_PATH", str(missing))
    monkeypatch.delenv("PREDICTION_JOURNAL_PATH", raising=False)

    response = TestClient(app).get("/v1/predictions/2024-04-01")

    assert response.status_code == 503
    assert response.json()["detail"] == "Prediction store not available"
