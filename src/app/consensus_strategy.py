"""Read-only consensus-confirmed shadow strategy for moneyline signals.

The strategy is an app/observability layer only. It reads stored prediction and
journal artifacts, classifies model/market directional agreement, and never
changes the baseline PLAY/PASS rule, market math, model, or prediction records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.board import DEFAULT_EDGE_THRESHOLD
from app.dashboard_analytics import (
    EDGE_BUCKET_SPECS,
    flat_stake_profit,
    is_play,
    latest_journal_by_key,
    prediction_key,
)
from market.engine import american_to_decimal, expected_value

MARKET_NEUTRAL_BAND = 0.02
LARGE_DISAGREEMENT_THRESHOLD = 0.08
MIN_MODEL_SIDE_PROBABILITY = 0.55
MIN_SELECTED_SIDE_EDGE = 0.01
STALE_ODDS_WINDOW = timedelta(hours=4)

MODEL_MARKET_AGREE_HOME = "MODEL_MARKET_AGREE_HOME"
MODEL_MARKET_AGREE_AWAY = "MODEL_MARKET_AGREE_AWAY"
MODEL_HOME_MARKET_NEUTRAL = "MODEL_HOME_MARKET_NEUTRAL"
MODEL_AWAY_MARKET_NEUTRAL = "MODEL_AWAY_MARKET_NEUTRAL"
MODEL_MARKET_DISAGREE = "MODEL_MARKET_DISAGREE"
LARGE_MODEL_MARKET_DISAGREEMENT = "LARGE_MODEL_MARKET_DISAGREEMENT"
MISSING_MARKET = "MISSING_MARKET"

CONSENSUS_RELATIONSHIP_BUCKETS: tuple[str, ...] = (
    MODEL_MARKET_AGREE_HOME,
    MODEL_MARKET_AGREE_AWAY,
    MODEL_HOME_MARKET_NEUTRAL,
    MODEL_AWAY_MARKET_NEUTRAL,
    MODEL_MARKET_DISAGREE,
    LARGE_MODEL_MARKET_DISAGREEMENT,
    MISSING_MARKET,
)

MODEL_PROBABILITY_BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("50-55%", 0.50, 0.55),
    ("55-60%", 0.55, 0.60),
    ("60-65%", 0.60, 0.65),
    ("65-70%", 0.65, 0.70),
    ("70%+", 0.70, None),
)

MAJOR_DATA_WARNING_FLAGS = {
    "DATA WARNING",
    "MISSING_REQUIRED_FIELDS",
    "MISSING_FEATURE_VALUES",
    "MISSING_ODDS",
    "STALE_ODDS",
    "GAME_ALREADY_STARTED",
}


def raw_model_side(row: Mapping[str, Any]) -> str:
    """Side implied only by stored model P(home), independent of edge."""
    p_home = _probability_or_none(row.get("model_probability"))
    if p_home is None:
        raise ValueError("model_probability must be numeric")
    return "HOME" if p_home >= 0.5 else "AWAY"


def market_side(row: Mapping[str, Any]) -> str | None:
    """Side implied only by stored no-vig market P(home)."""
    p_home = _probability_or_none(row.get("market_probability"))
    if p_home is None:
        return None
    return "HOME" if p_home >= 0.5 else "AWAY"


def edge_selected_side(row: Mapping[str, Any]) -> str | None:
    """Side implied by the sign of home-relative edge."""
    edge = _number_or_none(row.get("edge"))
    if edge is None:
        return None
    return "HOME" if edge >= 0.0 else "AWAY"


def classify_model_market_relationship(row: Mapping[str, Any]) -> str:
    model = raw_model_side(row)
    model_home_probability = _probability_or_none(row.get("model_probability"))
    market_home = _probability_or_none(row.get("market_probability"))
    if market_home is None:
        return MISSING_MARKET

    if (
        model_home_probability is not None
        and abs(model_home_probability - market_home) >= LARGE_DISAGREEMENT_THRESHOLD
    ):
        return LARGE_MODEL_MARKET_DISAGREEMENT

    model_home = model == "HOME"
    if abs(market_home - 0.5) <= MARKET_NEUTRAL_BAND:
        return MODEL_HOME_MARKET_NEUTRAL if model_home else MODEL_AWAY_MARKET_NEUTRAL

    market = "HOME" if market_home >= 0.5 else "AWAY"
    if market == model:
        return MODEL_MARKET_AGREE_HOME if model_home else MODEL_MARKET_AGREE_AWAY

    return MODEL_MARKET_DISAGREE


def selected_side_probabilities(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return model/market/edge values from the raw model favorite's side."""
    model = raw_model_side(row)
    model_home = _probability_or_none(row.get("model_probability"))
    market_home = _probability_or_none(row.get("market_probability"))
    if model_home is None:
        return {"selected_side": model, "model_probability": None, "market_probability": None, "edge": None}
    model_p = model_home if model == "HOME" else 1.0 - model_home
    market_p = None if market_home is None else market_home if model == "HOME" else 1.0 - market_home
    return {
        "selected_side": model,
        "model_probability": model_p,
        "market_probability": market_p,
        "edge": None if market_p is None else model_p - market_p,
    }


def consensus_confirmed_play(row: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """True when the row qualifies for the conservative SHADOW challenger."""
    snapshot = selected_side_probabilities(row)
    relationship = classify_model_market_relationship(row)
    allowed_relationship = relationship in {
        MODEL_MARKET_AGREE_HOME,
        MODEL_MARKET_AGREE_AWAY,
        MODEL_HOME_MARKET_NEUTRAL,
        MODEL_AWAY_MARKET_NEUTRAL,
    }
    return (
        allowed_relationship
        and snapshot["model_probability"] is not None
        and snapshot["market_probability"] is not None
        and snapshot["edge"] is not None
        and snapshot["model_probability"] >= MIN_MODEL_SIDE_PROBABILITY
        and snapshot["edge"] >= MIN_SELECTED_SIDE_EDGE
        and _selected_american(row, snapshot["selected_side"]) is not None
        and _fresh_when_metadata_exists(row, now=now)
        and not _has_major_data_warning(row)
    )


def prepare_consensus_candidate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = selected_side_probabilities(row)
    side = snapshot["selected_side"]
    american = _selected_american(row, side)
    ev = None
    if snapshot["model_probability"] is not None and american is not None:
        try:
            ev = expected_value(snapshot["model_probability"], american)
        except ValueError:
            ev = None
    return {
        "game_pk": row.get("game_pk"),
        "matchup": row.get("matchup"),
        "raw_model_side": side,
        "market_side": market_side(row),
        "edge_selected_side": edge_selected_side(row),
        "relationship_bucket": classify_model_market_relationship(row),
        "selected_model_probability": snapshot["model_probability"],
        "selected_market_probability": snapshot["market_probability"],
        "selected_side_edge": snapshot["edge"],
        "selected_american": american,
        "expected_value": ev,
        "baseline_play": is_play(row),
        "consensus_confirmed_play": consensus_confirmed_play(row),
        "strategy_label": "SHADOW DIAGNOSTIC - NOT PRODUCTION",
    }


def build_consensus_strategy_metrics(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Backtest consensus candidates from latest prediction per ``game_pk``."""
    latest = _latest_predictions_per_game(predictions)
    journal_by_key = latest_journal_by_key(journal)
    candidates = [row for row in latest if consensus_confirmed_play(row)]
    return strategy_metrics(candidates, journal_by_key, strategy="consensus_confirmed_play")


def strategy_metrics(
    selected_latest: Sequence[Mapping[str, Any]],
    journal_by_key: Mapping[tuple[Any, str | None], Mapping[str, Any]],
    *,
    strategy: str,
) -> dict[str, Any]:
    wins = losses = pending = 0
    profits: list[float] = []
    model_ps: list[float] = []
    market_ps: list[float] = []
    edges: list[float] = []
    odds: list[int] = []
    favorite_count = underdog_count = home_count = away_count = 0
    relationship_counts = {label: 0 for label in CONSENSUS_RELATIONSHIP_BUCKETS}
    prob_bucket_counts = {label: 0 for label, _, _ in MODEL_PROBABILITY_BUCKET_SPECS}
    edge_bucket_counts = {label: 0 for label, _, _ in EDGE_BUCKET_SPECS}

    for prediction in selected_latest:
        snapshot = selected_side_probabilities(prediction)
        side = snapshot["selected_side"]
        side_home = side == "HOME"
        model_p = snapshot["model_probability"]
        market_p = snapshot["market_probability"]
        selected_edge = snapshot["edge"]
        american = _selected_american(prediction, side)

        relationship = classify_model_market_relationship(prediction)
        relationship_counts[relationship] = relationship_counts.get(relationship, 0) + 1
        if model_p is not None:
            bucket = _bucket_label(model_p, MODEL_PROBABILITY_BUCKET_SPECS)
            if bucket is not None:
                prob_bucket_counts[bucket] = prob_bucket_counts.get(bucket, 0) + 1
        if selected_edge is not None:
            bucket = _bucket_label(abs(selected_edge), EDGE_BUCKET_SPECS)
            if bucket is not None:
                edge_bucket_counts[bucket] = edge_bucket_counts.get(bucket, 0) + 1

        favorite_count += 1
        if side_home:
            home_count += 1
        else:
            away_count += 1
        if american is not None:
            odds.append(american)

        enrichment = journal_by_key.get(prediction_key(prediction))
        if enrichment is None or not isinstance(enrichment.get("actual_home_win"), bool):
            pending += 1
            continue

        if model_p is not None:
            model_ps.append(model_p)
        if market_p is not None:
            market_ps.append(market_p)
        if selected_edge is not None:
            edges.append(selected_edge)

        actual_home_win = bool(enrichment["actual_home_win"])
        won = actual_home_win if side_home else not actual_home_win
        if won:
            wins += 1
        else:
            losses += 1
        profit = flat_stake_profit(won=won, american=american)
        if profit is not None:
            profits.append(profit)

    finished = wins + losses
    return {
        "strategy": strategy,
        "n": len(selected_latest),
        "n_resolved": finished,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / finished if finished else None,
        "roi": sum(profits) / len(profits) if profits else None,
        "units": float(sum(profits)) if profits else None,
        "staked_units": len(profits),
        "average_model_probability": sum(model_ps) / len(model_ps) if model_ps else None,
        "average_market_probability": sum(market_ps) / len(market_ps) if market_ps else None,
        "average_edge": sum(edges) / len(edges) if edges else None,
        "average_odds": sum(odds) / len(odds) if odds else None,
        "favorite_count": favorite_count,
        "underdog_count": underdog_count,
        "home_count": home_count,
        "away_count": away_count,
        "relationship_buckets": [
            {"bucket": label, "n": relationship_counts.get(label, 0)}
            for label in CONSENSUS_RELATIONSHIP_BUCKETS
        ],
        "model_probability_buckets": [
            {"bucket": label, "n": prob_bucket_counts.get(label, 0)}
            for label, _, _ in MODEL_PROBABILITY_BUCKET_SPECS
        ],
        "edge_buckets": [
            {"bucket": label, "n": edge_bucket_counts.get(label, 0)}
            for label, _, _ in EDGE_BUCKET_SPECS
        ],
    }


def latest_predictions_per_game(
    predictions: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return _latest_predictions_per_game(predictions)


def _selected_american(row: Mapping[str, Any], side: str) -> int | None:
    value = row.get("home_american") if side == "HOME" else row.get("away_american")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        american_to_decimal(value)
    except ValueError:
        return None
    return value


def _fresh_when_metadata_exists(row: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    odds_ts = _parse_timestamp(row.get("odds_snapshot_timestamp"))
    prediction_ts = _parse_timestamp(row.get("prediction_timestamp"))
    game_start = _parse_timestamp(row.get("game_start_timestamp"))
    if odds_ts is not None and prediction_ts is not None and odds_ts >= prediction_ts:
        return False
    if odds_ts is not None and prediction_ts is not None and prediction_ts - odds_ts > STALE_ODDS_WINDOW:
        return False
    if odds_ts is not None and now is not None and now.astimezone(timezone.utc) - odds_ts > STALE_ODDS_WINDOW:
        return False
    if prediction_ts is not None and game_start is not None and prediction_ts >= game_start:
        return False
    return True


def _has_major_data_warning(row: Mapping[str, Any]) -> bool:
    flags = row.get("risk_flags")
    if not isinstance(flags, Sequence) or isinstance(flags, (str, bytes)):
        return False
    return any(str(flag) in MAJOR_DATA_WARNING_FLAGS for flag in flags)


def _latest_predictions_per_game(
    predictions: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    latest: dict[Any, Mapping[str, Any]] = {}
    for prediction in predictions:
        game_pk = prediction.get("game_pk")
        current = latest.get(game_pk)
        if current is None or _timestamp_sort_key(prediction.get("prediction_timestamp")) >= _timestamp_sort_key(
            current.get("prediction_timestamp")
        ):
            latest[game_pk] = prediction
    return list(latest.values())


def _bucket_label(
    value: float,
    specs: Sequence[tuple[str, float, float | None]],
) -> str | None:
    for label, lower, upper in specs:
        if upper is None and value >= lower:
            return label
        if upper is not None and lower <= value < upper:
            return label
    return None


def _number_or_none(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _probability_or_none(value: Any) -> float | None:
    value = _number_or_none(value)
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value


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
    return parsed.astimezone(timezone.utc)


def _timestamp_sort_key(value: Any) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed
