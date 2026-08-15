"""APP-006 - artifact-backed homepage summary helpers.

This module is the testable half of the Streamlit landing page. It reads the
same JSON/report artifacts the deployed app can see and shapes them into plain
summary values. It deliberately does not open DuckDB, fetch odds, refresh MLB
data, train models, or recompute evaluation metrics.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.board import DEFAULT_EDGE_THRESHOLD


DEFAULT_PREDICTIONS_PATH = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL_PATH = Path("state/predictions/journal.jsonl")
DEFAULT_SKIPPED_PATH = Path("state/predictions/skipped.jsonl")
DEFAULT_HOLDOUT_REPORT_PATH = Path("reports/experiments/v1-holdout-2026.json")
DEFAULT_DIAGNOSTICS_REPORT_PATH = Path("reports/experiments/v1-model-diagnostics.json")

MODEL_IDENTITY = "V1 tuned XGBoost · expanding window · uncalibrated"
METHODOLOGY_LABEL = "ADR-006 locked V1 methodology"


@dataclass(frozen=True)
class ArtifactPaths:
    predictions: Path = DEFAULT_PREDICTIONS_PATH
    journal: Path = DEFAULT_JOURNAL_PATH
    skipped: Path = DEFAULT_SKIPPED_PATH
    holdout_report: Path = DEFAULT_HOLDOUT_REPORT_PATH
    diagnostics_report: Path = DEFAULT_DIAGNOSTICS_REPORT_PATH


def build_homepage_summary(paths: ArtifactPaths = ArtifactPaths()) -> dict[str, Any]:
    """Return a display-only homepage summary from local artifacts."""
    predictions = _read_jsonl(paths.predictions)
    journal = _read_jsonl(paths.journal)
    skipped = _read_jsonl(paths.skipped)
    latest_run_date = _latest_run_date(predictions)
    latest_predictions = [
        row for row in predictions if str(row.get("run_date")) == str(latest_run_date)
    ] if latest_run_date is not None else []
    latest_predictions = _latest_prediction_per_game(latest_predictions)
    latest_journal = _journal_for_predictions(journal, latest_predictions)
    latest_skipped = [
        row for row in skipped if str(row.get("run_date")) == str(latest_run_date)
    ] if latest_run_date is not None else []

    play_rows = [row for row in latest_predictions if _is_play(row)]
    no_play_rows = [row for row in latest_predictions if not _is_play(row)]
    finished_keys = set(latest_journal)
    play_results = [
        latest_journal[_prediction_key(row)]
        for row in play_rows
        if _prediction_key(row) in latest_journal
    ]
    wins = sum(1 for row in play_results if row.get("correct") is True)
    losses = sum(1 for row in play_results if row.get("correct") is False)
    play_pending = len(play_rows) - wins - losses
    full_journal = _journal_for_predictions(journal, predictions)
    play_performance_7d = _build_play_performance_7d(predictions, full_journal)

    missing_artifacts = [
        str(path)
        for path in (paths.predictions, paths.journal, paths.skipped)
        if not path.exists()
    ]

    return {
        "model_identity": MODEL_IDENTITY,
        "methodology_label": METHODOLOGY_LABEL,
        "latest_run_date": latest_run_date,
        "predictions_count": len(latest_predictions),
        "unique_games_count": len({row.get("game_pk") for row in latest_predictions}),
        "plays_count": len(play_rows),
        "no_play_count": len(no_play_rows),
        "finished_predictions_count": len(finished_keys),
        "play_wins": wins,
        "play_losses": losses,
        "play_pending": play_pending,
        "play_performance_7d": play_performance_7d,
        "slate_snapshot": {
            "plays": len(play_rows),
            "passes": len(no_play_rows),
            "awaiting": len(latest_skipped),
            "play_wins": wins,
            "play_losses": losses,
            "play_pending": play_pending,
        },
        "skipped_count": len(latest_skipped),
        "awaiting_data_count": len(latest_skipped),
        "skipped_reasons": dict(Counter(str(row.get("reason")) for row in latest_skipped)),
        "predictions_last_updated": _max_timestamp(
            row.get("prediction_timestamp") for row in latest_predictions
        ),
        "odds_last_updated": _max_timestamp(
            row.get("odds_snapshot_timestamp") for row in latest_predictions
        ),
        "results_last_refreshed": _max_timestamp(
            row.get("enrichment_timestamp") for row in latest_journal.values()
        ),
        "artifact_paths": {
            "predictions": str(paths.predictions),
            "journal": str(paths.journal),
            "skipped": str(paths.skipped),
            "holdout_report": str(paths.holdout_report),
            "diagnostics_report": str(paths.diagnostics_report),
        },
        "missing_artifacts": missing_artifacts,
        "holdout_metrics": _load_holdout_metrics(paths.holdout_report),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _latest_run_date(predictions: Iterable[Mapping[str, Any]]) -> str | None:
    dates = sorted(
        {
            str(row.get("run_date"))
            for row in predictions
            if row.get("run_date") is not None
        }
    )
    return dates[-1] if dates else None


def _journal_for_predictions(
    journal: Iterable[Mapping[str, Any]],
    predictions: Iterable[Mapping[str, Any]],
) -> dict[tuple[Any, str | None], Mapping[str, Any]]:
    wanted = {_prediction_key(row) for row in predictions}
    matches: dict[tuple[Any, str | None], Mapping[str, Any]] = {}
    for row in journal:
        key = _prediction_key(row)
        if key in wanted:
            current = matches.get(key)
            if current is None or str(row.get("enrichment_timestamp")) >= str(
                current.get("enrichment_timestamp")
            ):
                matches[key] = row
    return matches


def _latest_prediction_per_game(
    predictions: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    latest: dict[Any, Mapping[str, Any]] = {}
    for row in predictions:
        game_pk = row.get("game_pk")
        current = latest.get(game_pk)
        if current is None:
            latest[game_pk] = row
            continue
        row_timestamp = _parse_timestamp(row.get("prediction_timestamp"))
        current_timestamp = _parse_timestamp(current.get("prediction_timestamp"))
        if row_timestamp is not None and current_timestamp is not None:
            if row_timestamp >= current_timestamp:
                latest[game_pk] = row
        elif row_timestamp is not None:
            latest[game_pk] = row
    return list(latest.values())


def _prediction_key(row: Mapping[str, Any]) -> tuple[Any, str | None]:
    timestamp = row.get("prediction_timestamp")
    return (row.get("game_pk"), str(timestamp) if timestamp is not None else None)


def _is_play(row: Mapping[str, Any]) -> bool:
    edge = row.get("edge")
    return (
        isinstance(edge, (int, float))
        and not isinstance(edge, bool)
        and abs(edge) >= DEFAULT_EDGE_THRESHOLD
    )


def _build_play_performance_7d(
    predictions: Iterable[Mapping[str, Any]],
    journal: Mapping[tuple[Any, str | None], Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate play-only W/L over the last seven prediction slates."""
    prediction_list = list(predictions)
    run_dates = sorted(
        {
            str(row.get("run_date"))
            for row in prediction_list
            if row.get("run_date") is not None
        }
    )[-7:]
    daily: list[dict[str, Any]] = []
    total_wins = 0
    total_losses = 0
    total_pending = 0
    for run_date in run_dates:
        day_predictions = [
            row
            for row in prediction_list
            if str(row.get("run_date")) == run_date
        ]
        play_rows = [
            row for row in _latest_prediction_per_game(day_predictions) if _is_play(row)
        ]
        day_wins = 0
        day_losses = 0
        day_pending = 0
        for row in play_rows:
            enrichment = journal.get(_prediction_key(row))
            if enrichment is None:
                day_pending += 1
                continue
            if enrichment.get("correct") is True:
                day_wins += 1
            elif enrichment.get("correct") is False:
                day_losses += 1
            else:
                day_pending += 1
        finished = day_wins + day_losses
        daily.append(
            {
                "run_date": run_date,
                "wins": day_wins,
                "losses": day_losses,
                "pending": day_pending,
                "win_rate": day_wins / finished if finished else None,
            }
        )
        total_wins += day_wins
        total_losses += day_losses
        total_pending += day_pending
    finished_total = total_wins + total_losses
    return {
        "win_rate": total_wins / finished_total if finished_total else None,
        "wins": total_wins,
        "losses": total_losses,
        "pending": total_pending,
        "finished": finished_total,
        "daily": daily,
    }


def _max_timestamp(values: Iterable[Any]) -> str | None:
    parsed = [_parse_timestamp(value) for value in values]
    valid = [value for value in parsed if value is not None]
    if not valid:
        return None
    return max(valid).astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_holdout_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(report, dict):
        return {}
    metrics = report.get("metrics")
    if isinstance(metrics, dict):
        loaded = {
            key: metrics[key]
            for key in ("log_loss", "brier", "ece", "roc_auc", "accuracy")
            if key in metrics
        }
        secondary = metrics.get("secondary")
        if isinstance(secondary, dict):
            for key in ("roc_auc", "accuracy"):
                if key in secondary and key not in loaded:
                    loaded[key] = secondary[key]
        return loaded
    return {
        key: report[key]
        for key in ("log_loss", "brier", "ece", "roc_auc", "accuracy")
        if key in report
    }
