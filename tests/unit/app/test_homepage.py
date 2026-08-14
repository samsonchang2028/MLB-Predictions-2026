from __future__ import annotations

import json
from pathlib import Path

from app.homepage import ArtifactPaths, build_homepage_summary


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_homepage_summary_counts_latest_slate_and_excludes_pass_from_record(tmp_path: Path) -> None:
    predictions = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    skipped = tmp_path / "skipped.jsonl"
    holdout = tmp_path / "holdout.json"
    diagnostics = tmp_path / "diagnostics.json"
    _write_jsonl(
        predictions,
        [
            {
                "game_pk": 1,
                "run_date": "2026-08-13",
                "edge": 0.03,
                "prediction_timestamp": "2026-08-13T16:00:00+00:00",
                "odds_snapshot_timestamp": "2026-08-13T15:59:00+00:00",
            },
            {
                "game_pk": 2,
                "run_date": "2026-08-13",
                "edge": 0.005,
                "prediction_timestamp": "2026-08-13T16:01:00+00:00",
                "odds_snapshot_timestamp": "2026-08-13T15:58:00+00:00",
            },
            {
                "game_pk": 3,
                "run_date": "2026-08-12",
                "edge": 0.10,
                "prediction_timestamp": "2026-08-12T16:00:00+00:00",
                "odds_snapshot_timestamp": "2026-08-12T15:59:00+00:00",
            },
        ],
    )
    _write_jsonl(
        journal,
        [
            {
                "game_pk": 1,
                "prediction_timestamp": "2026-08-13T16:00:00+00:00",
                "correct": True,
                "enrichment_timestamp": "2026-08-14T06:00:00+00:00",
            },
            {
                "game_pk": 2,
                "prediction_timestamp": "2026-08-13T16:01:00+00:00",
                "correct": False,
                "enrichment_timestamp": "2026-08-14T06:01:00+00:00",
            },
        ],
    )
    _write_jsonl(skipped, [{"run_date": "2026-08-13", "reason": "no_odds_snapshot"}])
    holdout.write_text(json.dumps({"metrics": {"log_loss": 0.68, "brier": 0.24}}), encoding="utf-8")
    diagnostics.write_text("{}", encoding="utf-8")

    summary = build_homepage_summary(
        ArtifactPaths(
            predictions=predictions,
            journal=journal,
            skipped=skipped,
            holdout_report=holdout,
            diagnostics_report=diagnostics,
        )
    )

    assert summary["latest_run_date"] == "2026-08-13"
    assert summary["predictions_count"] == 2
    assert summary["unique_games_count"] == 2
    assert summary["plays_count"] == 1
    assert summary["no_play_count"] == 1
    assert summary["finished_predictions_count"] == 2
    assert summary["play_wins"] == 1
    assert summary["play_losses"] == 0
    assert summary["play_pending"] == 0
    assert summary["skipped_count"] == 1
    assert summary["awaiting_data_count"] == 1
    assert summary["skipped_reasons"] == {"no_odds_snapshot": 1}
    assert summary["predictions_last_updated"] == "2026-08-13T16:01:00+00:00"
    assert summary["odds_last_updated"] == "2026-08-13T15:59:00+00:00"
    assert summary["results_last_refreshed"] == "2026-08-14T06:01:00+00:00"
    assert summary["holdout_metrics"] == {"log_loss": 0.68, "brier": 0.24}


def test_homepage_summary_reports_missing_artifacts(tmp_path: Path) -> None:
    paths = ArtifactPaths(
        predictions=tmp_path / "missing-daily.jsonl",
        journal=tmp_path / "missing-journal.jsonl",
        skipped=tmp_path / "missing-skipped.jsonl",
        holdout_report=tmp_path / "missing-holdout.json",
        diagnostics_report=tmp_path / "missing-diagnostics.json",
    )

    summary = build_homepage_summary(paths)

    assert summary["latest_run_date"] is None
    assert summary["predictions_count"] == 0
    assert summary["plays_count"] == 0
    assert summary["play_wins"] == 0
    assert summary["awaiting_data_count"] == 0
    assert summary["missing_artifacts"] == [
        str(paths.predictions),
        str(paths.journal),
        str(paths.skipped),
    ]


def test_homepage_summary_counts_only_latest_prediction_per_game(tmp_path: Path) -> None:
    predictions = tmp_path / "daily.jsonl"
    journal = tmp_path / "journal.jsonl"
    skipped = tmp_path / "skipped.jsonl"
    holdout = tmp_path / "holdout.json"
    diagnostics = tmp_path / "diagnostics.json"
    _write_jsonl(
        predictions,
        [
            {
                "game_pk": 7,
                "run_date": "2026-08-13",
                "edge": 0.05,
                "prediction_timestamp": "2026-08-13T16:00:00+00:00",
                "odds_snapshot_timestamp": "2026-08-13T15:59:00+00:00",
            },
            {
                "game_pk": 7,
                "run_date": "2026-08-13",
                "edge": 0.005,
                "prediction_timestamp": "2026-08-13T17:00:00+00:00",
                "odds_snapshot_timestamp": "2026-08-13T16:59:00+00:00",
            },
        ],
    )
    _write_jsonl(
        journal,
        [
            {
                "game_pk": 7,
                "prediction_timestamp": "2026-08-13T16:00:00+00:00",
                "correct": True,
                "enrichment_timestamp": "2026-08-14T06:00:00+00:00",
            },
        ],
    )
    skipped.write_text("", encoding="utf-8")
    holdout.write_text("{}", encoding="utf-8")
    diagnostics.write_text("{}", encoding="utf-8")

    summary = build_homepage_summary(
        ArtifactPaths(
            predictions=predictions,
            journal=journal,
            skipped=skipped,
            holdout_report=holdout,
            diagnostics_report=diagnostics,
        )
    )

    assert summary["predictions_count"] == 1
    assert summary["unique_games_count"] == 1
    assert summary["plays_count"] == 0
    assert summary["no_play_count"] == 1
    assert summary["finished_predictions_count"] == 0
    assert summary["predictions_last_updated"] == "2026-08-13T17:00:00+00:00"
