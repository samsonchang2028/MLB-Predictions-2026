from __future__ import annotations

import json
from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from pipelines.daily import JsonLinesPredictionStore
from scripts import enrich_prediction_results as er
from scripts.enrich_prediction_results import load_completed_result_rows, run


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _write_prediction(path: Path, *, game_pk: int = 1, run_date: str = "2026-08-13") -> None:
    store = JsonLinesPredictionStore(path)
    store.append(
        {
            "game_pk": game_pk,
            "prediction_timestamp": _dt("2026-08-13T19:00:00"),
            "model_version": "v1",
            "feature_schema_version": "schema",
            "build_id": "build",
            "model_probability": 0.62,
            "odds_snapshot_timestamp": _dt("2026-08-13T18:55:00"),
            "market_probability": 0.54,
            "edge": 0.08,
            "source": "the_odds_api:draftkings",
            "home_team_id": 10,
            "away_team_id": 11,
            "game_start_timestamp": _dt("2026-08-13T20:00:00"),
            "run_date": run_date,
        }
    )


def _write_silver(database: Path, *, state: str = "Final", coded_state: str = "F") -> None:
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
        connection.execute(
            """
            CREATE TABLE silver.games (
                game_pk BIGINT,
                official_date DATE,
                game_type VARCHAR,
                abstract_game_state VARCHAR,
                coded_game_state VARCHAR
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE silver.team_game_statistics (
                game_pk BIGINT,
                team_id BIGINT,
                side VARCHAR,
                score INTEGER,
                is_winner BOOLEAN
            )
            """
        )
        connection.execute(
            "INSERT INTO silver.games VALUES (1, ?, 'R', ?, ?)",
            [date(2026, 8, 13), state, coded_state],
        )
        connection.executemany(
            "INSERT INTO silver.team_game_statistics VALUES (?, ?, ?, ?, ?)",
            [(1, 10, "home", 5, True), (1, 11, "away", 3, False)],
        )


def _args(database: Path, predictions: Path, journal: Path, *, skip_schedule_refresh: bool) -> Namespace:
    return Namespace(
        date="2026-08-13",
        database=str(database),
        predictions=str(predictions),
        journal=str(journal),
        enrichment_timestamp="2026-08-14T06:00:00+00:00",
        skip_schedule_refresh=skip_schedule_refresh,
    )


def test_load_completed_result_rows_reads_only_final_regular_games(tmp_path: Path) -> None:
    database = tmp_path / "mlb.duckdb"
    _write_silver(database, state="Live", coded_state="I")

    with duckdb.connect(str(database), read_only=True) as connection:
        assert load_completed_result_rows(connection, date(2026, 8, 13), game_pks=[1]) == []


def test_run_writes_separate_journal_with_score_without_touching_predictions(tmp_path: Path) -> None:
    database = tmp_path / "mlb.duckdb"
    predictions = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    _write_silver(database)
    _write_prediction(predictions)
    before = predictions.read_bytes()

    assert run(_args(database, predictions, journal, skip_schedule_refresh=True)) == 0

    assert predictions.read_bytes() == before
    [row] = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert row["actual_home_win"] is True
    assert row["correct"] is True
    assert row["home_score"] == 5
    assert row["away_score"] == 3


def test_run_refreshes_schedule_before_loading_results(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "mlb.duckdb"
    predictions = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    _write_silver(database)
    _write_prediction(predictions)
    calls: list[str] = []

    def fake_refresh(database_arg, run_date):
        calls.append(f"refresh:{run_date}")
        assert str(database_arg) == str(database)
        return {"games_seen": 1, "payload_sha256": "abc"}

    monkeypatch.setattr(er, "refresh_schedule_results", fake_refresh)

    assert run(_args(database, predictions, journal, skip_schedule_refresh=False)) == 0
    assert calls == ["refresh:2026-08-13"]


def test_main_prints_enrichment_summary(monkeypatch, tmp_path: Path, capsys) -> None:
    database = tmp_path / "mlb.duckdb"
    predictions = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    _write_silver(database)
    _write_prediction(predictions)
    monkeypatch.setattr(er, "refresh_schedule_results", lambda *_args: {"games_seen": 1})

    assert er.main([
        "--database", str(database),
        "--predictions", str(predictions),
        "--journal", str(journal),
        "--date", "2026-08-13",
        "--enrichment-timestamp", "2026-08-14T06:00:00+00:00",
    ]) == 0

    assert "[journal] records=1 written=1 skipped=0" in capsys.readouterr().out
