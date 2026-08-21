"""APP-013 - artifact-backed analytics for separated Streamlit dashboards.

Reads prediction + journal JSONL and committed experiment reports only. Shapes
display rows and aggregates for model-quality, market-edge, betting-result, and
prospective-evaluation views.

Prospective probability metrics (log loss, Brier, ECE, etc.) are computed from
stored ``model_probability`` and journaled ``actual_home_win`` — not from
re-running the model. Betting ROI uses stored American odds on the displayed
pick side with a flat 1-unit stake when odds are present.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score

from app.board import DEFAULT_EDGE_THRESHOLD
from market.engine import american_to_decimal

HISTORICAL_EVIDENCE_LABEL = "Historical evaluation"
PROSPECTIVE_EVIDENCE_LABEL = "Prospective production monitoring"
BETTING_RESULTS_NOTE = (
    "Betting/selection results, not model-training or holdout evidence."
)
PROSPECTIVE_MONITORING_NOTE = (
    "Prospective monitoring data. Not automatically used for retraining."
)
EDGE_PROFITABILITY_NOTE = (
    "Edge measures model-vs-market disagreement. It does not prove profitability."
)

PROBABILITY_BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("<45%", 0.0, 0.45),
    ("45–50%", 0.45, 0.50),
    ("50–55%", 0.50, 0.55),
    ("55–60%", 0.55, 0.60),
    ("60–65%", 0.60, 0.65),
    ("65%+", 0.65, None),
)

EDGE_BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("0–2%", 0.0, 0.02),
    ("2–4%", 0.02, 0.04),
    ("4–6%", 0.04, 0.06),
    ("6–8%", 0.06, 0.08),
    ("8%+", 0.08, None),
)


@dataclass(frozen=True)
class DashboardPaths:
    predictions: Path = Path("state/predictions/daily.jsonl")
    journal: Path = Path("state/predictions/journal.jsonl")
    holdout_report: Path = Path("reports/experiments/v1-holdout-2026.json")
    development_report: Path = Path(
        "reports/experiments/v1-repaired-a910017bac839af5.json"
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def prediction_key(row: Mapping[str, Any]) -> tuple[Any, str | None]:
    timestamp = row.get("prediction_timestamp")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    return (row.get("game_pk"), str(timestamp) if timestamp is not None else None)


def latest_journal_by_key(
    journal: Iterable[Mapping[str, Any]],
) -> dict[tuple[Any, str | None], Mapping[str, Any]]:
    matches: dict[tuple[Any, str | None], Mapping[str, Any]] = {}
    for row in journal:
        key = prediction_key(row)
        current = matches.get(key)
        if current is None or str(row.get("enrichment_timestamp")) >= str(
            current.get("enrichment_timestamp")
        ):
            matches[key] = row
    return matches


def is_play(row: Mapping[str, Any], *, edge_threshold: float = DEFAULT_EDGE_THRESHOLD) -> bool:
    edge = row.get("edge")
    return (
        isinstance(edge, (int, float))
        and not isinstance(edge, bool)
        and abs(float(edge)) >= edge_threshold
    )


def picked_home(row: Mapping[str, Any]) -> bool:
    edge = row.get("edge")
    if isinstance(edge, (int, float)) and not isinstance(edge, bool):
        return float(edge) >= 0.0
    model_probability = row.get("model_probability")
    if isinstance(model_probability, (int, float)) and not isinstance(model_probability, bool):
        return float(model_probability) >= 0.5
    return True


def model_predicted_home(row: Mapping[str, Any]) -> bool:
    """Return the model's modal winner from the stored P(home).

    This is deliberately separate from :func:`picked_home`: the displayed
    market-relative side follows the sign of edge, while the model winner
    follows the 0.5 probability boundary.
    """
    model_probability = row.get("model_probability")
    if not isinstance(model_probability, (int, float)) or isinstance(
        model_probability, bool
    ):
        raise ValueError("model_probability must be numeric")
    return float(model_probability) >= 0.5


def selected_side_probability(row: Mapping[str, Any]) -> float | None:
    model_probability = row.get("model_probability")
    if not isinstance(model_probability, (int, float)) or isinstance(model_probability, bool):
        return None
    p_home = float(model_probability)
    return p_home if picked_home(row) else 1.0 - p_home


def selected_side_market_probability(row: Mapping[str, Any]) -> float | None:
    market_probability = row.get("market_probability")
    if not isinstance(market_probability, (int, float)) or isinstance(market_probability, bool):
        return None
    p_home = float(market_probability)
    return p_home if picked_home(row) else 1.0 - p_home


def pick_american_odds(row: Mapping[str, Any]) -> int | None:
    field = "home_american" if picked_home(row) else "away_american"
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def flat_stake_profit(*, won: bool, american: int | None) -> float | None:
    if american is None:
        return None
    if won:
        return american_to_decimal(american) - 1.0
    return -1.0


def resolved_prediction_rows(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join the latest displayed prediction per game to its journal result."""
    journal_by_key = latest_journal_by_key(journal)
    rows: list[dict[str, Any]] = []
    for prediction in _latest_predictions_per_game(predictions):
        key = prediction_key(prediction)
        enrichment = journal_by_key.get(key)
        if enrichment is None:
            continue
        actual_home_win = enrichment.get("actual_home_win")
        if not isinstance(actual_home_win, bool):
            continue
        rows.append(
            {
                "game_pk": prediction.get("game_pk"),
                "run_date": prediction.get("run_date"),
                "prediction_timestamp": prediction.get("prediction_timestamp"),
                "model_version": prediction.get("model_version"),
                "model_probability": float(prediction["model_probability"]),
                "market_probability": float(prediction["market_probability"]),
                "edge": float(prediction["edge"]),
                "actual_home_win": actual_home_win,
                "correct": enrichment.get("correct"),
                "play": is_play(prediction),
                "model_predicted_home": model_predicted_home(prediction),
                "picked_home": picked_home(prediction),
                "selected_model_probability": selected_side_probability(prediction),
                "selected_market_probability": selected_side_market_probability(prediction),
                "pick_american": pick_american_odds(prediction),
                "home_american": prediction.get("home_american"),
                "away_american": prediction.get("away_american"),
                "source": prediction.get("source"),
            }
        )
    rows.sort(key=lambda row: (str(row.get("run_date")), row["game_pk"], str(row.get("prediction_timestamp"))))
    return rows


def compute_probability_metrics(
    y_true: Sequence[bool | int],
    p_home: Sequence[float],
) -> dict[str, Any] | None:
    if not y_true:
        return None
    labels = np.array([1 if bool(value) else 0 for value in y_true], dtype=int)
    probs = np.array(p_home, dtype=float)
    single_class = np.unique(labels).size < 2
    return {
        "n": int(len(labels)),
        "log_loss": float(log_loss(labels, probs, labels=[0, 1])),
        "brier": float(brier_score_loss(labels, probs, pos_label=1)),
        "ece": float(_expected_calibration_error(labels, probs)),
        "accuracy": float(accuracy_score(labels, (probs >= 0.5).astype(int))),
        "roc_auc": None if single_class else float(roc_auc_score(labels, probs)),
        "positive_rate": float(labels.mean()),
    }


def build_prospective_model_quality(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    resolved = resolved_prediction_rows(predictions, journal)
    pending = _pending_prediction_count(predictions, journal)
    run_dates = sorted(
        {str(row.get("run_date")) for row in predictions if row.get("run_date") is not None}
    )
    metrics = compute_probability_metrics(
        [row["actual_home_win"] for row in resolved],
        [row["model_probability"] for row in resolved],
    )
    model_versions = sorted(
        {str(row.get("model_version")) for row in predictions if row.get("model_version")}
    )
    return {
        "evidence_label": PROSPECTIVE_EVIDENCE_LABEL,
        "note": PROSPECTIVE_MONITORING_NOTE,
        "evaluation_start_date": run_dates[0] if run_dates else None,
        "latest_run_date": run_dates[-1] if run_dates else None,
        "model_versions": model_versions,
        "resolved_count": len(resolved),
        "pending_count": pending,
        "metrics": metrics,
        "probability_buckets": build_probability_buckets(resolved),
        "reliability_buckets": build_probability_buckets(resolved),
    }


def build_market_edge_summary(
    board_rows: Sequence[Mapping[str, Any]],
    *,
    resolved_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    edges = [
        float(row["edge"])
        for row in board_rows
        if isinstance(row.get("edge"), (int, float)) and not isinstance(row.get("edge"), bool)
    ]
    disagreements = sorted(
        (
            {
                "game_pk": row.get("game_pk"),
                "matchup": row.get("matchup"),
                "edge": float(row["edge"]),
                "model_probability": row.get("model_probability"),
                "market_probability": row.get("market_probability"),
                "pick": row.get("pick") or row.get("model_side"),
                "recommendation": row.get("recommendation") or row.get("action_label"),
            }
            for row in board_rows
            if isinstance(row.get("edge"), (int, float)) and not isinstance(row.get("edge"), bool)
        ),
        key=lambda item: (-abs(item["edge"]), item["game_pk"] if item["game_pk"] is not None else 0),
    )
    summary = {
        "note": EDGE_PROFITABILITY_NOTE,
        "game_count": len(board_rows),
        "average_edge": float(np.mean(edges)) if edges else None,
        "average_abs_edge": float(np.mean(np.abs(edges))) if edges else None,
        "edge_min": float(min(edges)) if edges else None,
        "edge_max": float(max(edges)) if edges else None,
        "largest_disagreements": disagreements[:10],
        "edge_distribution": _histogram(edges, EDGE_BUCKET_SPECS, use_abs=True),
    }
    if resolved_rows is not None:
        summary["edge_bucket_performance"] = build_edge_buckets(resolved_rows)
    return summary


def build_betting_results_summary(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    latest_plays = [
        row for row in _latest_predictions_per_game(predictions) if is_play(row)
    ]
    resolved = resolved_prediction_rows(predictions, journal)
    play_rows = [row for row in resolved if row["play"]]
    wins = sum(1 for row in play_rows if row.get("correct") is True)
    losses = sum(1 for row in play_rows if row.get("correct") is False)
    pending = len(latest_plays) - len(play_rows)
    profits = [
        profit
        for row in play_rows
        if (profit := flat_stake_profit(
            won=row.get("correct") is True,
            american=row.get("pick_american"),
        )) is not None
    ]
    odds_values = [row["pick_american"] for row in play_rows if row.get("pick_american") is not None]
    staked = len(profits)
    return {
        "note": BETTING_RESULTS_NOTE,
        "play_count": len(latest_plays),
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / (wins + losses) if wins + losses else None,
        "roi": sum(profits) / staked if staked else None,
        "units": float(sum(profits)) if profits else None,
        "staked_units": staked,
        "average_edge": float(np.mean([abs(row["edge"]) for row in play_rows])) if play_rows else None,
        "average_odds": float(np.mean(odds_values)) if odds_values else None,
        "edge_bucket_performance": build_edge_buckets(play_rows, play_only=True),
        "missing_odds_count": sum(1 for row in play_rows if row.get("pick_american") is None),
    }


def build_probability_buckets(resolved_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for label, lower, upper in PROBABILITY_BUCKET_SPECS:
        members = [
            row
            for row in resolved_rows
            if _in_bucket(row["model_probability"], lower, upper)
        ]
        if not members:
            buckets.append(
                {
                    "bucket": label,
                    "n": 0,
                    "average_predicted_p": None,
                    "actual_win_rate": None,
                    "calibration_gap": None,
                }
            )
            continue
        predicted = float(np.mean([row["model_probability"] for row in members]))
        actual = float(np.mean([1.0 if row["actual_home_win"] else 0.0 for row in members]))
        buckets.append(
            {
                "bucket": label,
                "n": len(members),
                "average_predicted_p": predicted,
                "actual_win_rate": actual,
                "calibration_gap": actual - predicted,
            }
        )
    return buckets


def build_edge_buckets(
    resolved_rows: Sequence[Mapping[str, Any]],
    *,
    play_only: bool = False,
) -> list[dict[str, Any]]:
    rows = [row for row in resolved_rows if row.get("play")] if play_only else list(resolved_rows)
    buckets: list[dict[str, Any]] = []
    for label, lower, upper in EDGE_BUCKET_SPECS:
        members = [
            row for row in rows if _in_bucket(abs(float(row["edge"])), lower, upper)
        ]
        profits = [
            profit
            for row in members
            if (profit := flat_stake_profit(
                won=row.get("correct") is True,
                american=row.get("pick_american"),
            )) is not None
        ]
        wins = sum(1 for row in members if row.get("correct") is True)
        finished = sum(1 for row in members if row.get("correct") is not None)
        buckets.append(
            {
                "bucket": label,
                "n": len(members),
                "average_edge": float(np.mean([abs(row["edge"]) for row in members]))
                if members
                else None,
                "result_rate": wins / finished if finished else None,
                "roi": sum(profits) / len(profits) if profits else None,
                "staked_units": len(profits),
            }
        )
    return buckets


def _pending_prediction_count(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> int:
    journal_by_key = latest_journal_by_key(journal)
    pending = 0
    for prediction in _latest_predictions_per_game(predictions):
        key = prediction_key(prediction)
        if key not in journal_by_key:
            pending += 1
    return pending


def _latest_predictions_per_game(
    predictions: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Collapse pregame refreshes using the board's latest-per-game policy."""
    latest: dict[Any, Mapping[str, Any]] = {}
    for prediction in predictions:
        game_pk = prediction.get("game_pk")
        current = latest.get(game_pk)
        if current is None or _prediction_timestamp_sort_key(
            prediction
        ) >= _prediction_timestamp_sort_key(current):
            latest[game_pk] = prediction
    return list(latest.values())


def _prediction_timestamp_sort_key(row: Mapping[str, Any]) -> datetime:
    value = row.get("prediction_timestamp")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    else:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_bucket(value: float, lower: float, upper: float | None) -> bool:
    if upper is None:
        return value >= lower
    return lower <= value < upper


def _histogram(
    values: Sequence[float],
    specs: Sequence[tuple[str, float, float | None]],
    *,
    use_abs: bool = False,
) -> list[dict[str, Any]]:
    processed = [abs(value) for value in values] if use_abs else list(values)
    return [
        {
            "bucket": label,
            "count": sum(
                1 for value in processed if _in_bucket(value, lower, upper)
            ),
        }
        for label, lower, upper in specs
    ]


def _expected_calibration_error(
    y_true: np.ndarray, p: np.ndarray, n_bins: int = 10
) -> float:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    total = len(p)
    ece = 0.0
    for bucket in range(n_bins):
        mask = bin_ids == bucket
        count = int(mask.sum())
        if count == 0:
            continue
        confidence = float(p[mask].mean())
        accuracy = float(y_true[mask].mean())
        ece += (count / total) * abs(accuracy - confidence)
    return ece
