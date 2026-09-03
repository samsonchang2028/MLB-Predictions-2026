"""Read-only uncertainty-adjusted challenger for daily moneyline signals.

The challenger is a shadow display layer. It does not change prediction
generation, model probabilities, market edge math, or baseline PLAY/PASS labels.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.board import DEFAULT_EDGE_THRESHOLD
from market.engine import expected_value

MIN_BUCKET_N = 30
FAVORITE_MODEL_PROBABILITY_MIN = 0.55
FAVORITE_EDGE_MIN = 0.03
LOWER_BOUND_EDGE_MIN = 0.01
LARGE_DISAGREEMENT_EDGE = 0.08
STALE_ODDS_HOURS = 4
STARTING_SOON_MINUTES = 30
WILSON_Z = 1.96

BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("50-55%", 0.50, 0.55),
    ("55-60%", 0.55, 0.60),
    ("60-65%", 0.60, 0.65),
    ("65-70%", 0.65, 0.70),
    ("70%+", 0.70, None),
)

BLOCKING_FLAGS = {
    "STALE_ODDS",
    "MISSING_ODDS",
    "GAME_ALREADY_STARTED",
    "GAME_STARTING_SOON",
    "LOW_SAMPLE_UNCERTAINTY",
    "MISSING_REQUIRED_FIELDS",
}


def raw_model_favorite_home(row: Mapping[str, Any]) -> bool:
    """Return the model favorite from stored P(home team wins)."""
    p_home = _probability_or_none(row.get("model_probability"))
    if p_home is None:
        raise ValueError("model_probability must be numeric")
    return p_home >= 0.5


def raw_model_favorite_side(row: Mapping[str, Any]) -> str:
    return "HOME" if raw_model_favorite_home(row) else "AWAY"


def selected_model_probability(row: Mapping[str, Any]) -> float | None:
    p_home = _probability_or_none(row.get("model_probability"))
    if p_home is None:
        return None
    return p_home if p_home >= 0.5 else 1.0 - p_home


def selected_market_probability(row: Mapping[str, Any]) -> float | None:
    p_market_home = _probability_or_none(row.get("market_probability"))
    p_model_home = _probability_or_none(row.get("model_probability"))
    if p_market_home is None or p_model_home is None:
        return None
    return p_market_home if p_model_home >= 0.5 else 1.0 - p_market_home


def favorite_edge(row: Mapping[str, Any]) -> float | None:
    model_p = selected_model_probability(row)
    market_p = selected_market_probability(row)
    if model_p is None or market_p is None:
        return None
    return model_p - market_p


def wins_per_100(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}"


def percentage_points(value: float | None) -> str:
    if value is None:
        return "-"
    points = value * 100
    sign = "+" if points >= 0 else ""
    return f"{sign}{points:.1f} pp"


def probability_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    for label, lower, upper in BUCKET_SPECS:
        if upper is None and value >= lower:
            return label
        if upper is not None and lower <= value < upper:
            return label
    return None


def wilson_interval(wins: int, n: int, *, z: float = WILSON_Z) -> tuple[float, float] | None:
    """Wilson score interval for a binomial win rate."""
    if n <= 0:
        return None
    if wins < 0 or wins > n:
        raise ValueError("wins must be between 0 and n")
    phat = wins / n
    denominator = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denominator
    spread = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))


def build_uncertainty_profile(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
    *,
    exclude_game_pk: Any | None = None,
    as_of_timestamp: datetime | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build raw-favorite win-rate buckets from resolved latest-per-game rows.

    Pending games are excluded. Multiple prediction snapshots are collapsed to
    the latest prediction per ``game_pk`` before joining to the journal.

    Live board candidate rows use the full resolved history (``exclude_game_pk``
    unset, ``as_of_timestamp`` unset). Shadow backtest selection passes
    ``exclude_game_pk`` and ``as_of_timestamp`` so a game is judged only against
    resolved games that had already started before the scored game's decision
    time, not its own outcome or games whose first pitch is still in the future.
    """
    journal_by_key = _latest_journal_by_key(journal)
    as_of = _parse_timestamp(as_of_timestamp) if as_of_timestamp is not None else None
    buckets = {
        label: {"bucket": label, "n": 0, "wins": 0, "win_rate": None, "lower": None, "upper": None}
        for label, _, _ in BUCKET_SPECS
    }
    for prediction in _latest_predictions_per_game(predictions):
        if exclude_game_pk is not None and prediction.get("game_pk") == exclude_game_pk:
            continue
        if as_of is not None and not _resolved_before_as_of(prediction, as_of):
            continue
        enrichment = journal_by_key.get(_prediction_key(prediction))
        if enrichment is None or not isinstance(enrichment.get("actual_home_win"), bool):
            continue
        model_p = selected_model_probability(prediction)
        bucket = probability_bucket(model_p)
        if bucket is None:
            continue
        favorite_home = raw_model_favorite_home(prediction)
        actual_home_win = bool(enrichment["actual_home_win"])
        favorite_won = actual_home_win if favorite_home else not actual_home_win
        buckets[bucket]["n"] += 1
        buckets[bucket]["wins"] += 1 if favorite_won else 0

    for row in buckets.values():
        if row["n"]:
            row["win_rate"] = row["wins"] / row["n"]
            interval = wilson_interval(row["wins"], row["n"])
            if interval is not None:
                row["lower"], row["upper"] = interval
        row["low_sample"] = row["n"] < MIN_BUCKET_N
    return buckets


def prepare_uncertainty_candidate_rows(
    board_rows: Sequence[Mapping[str, Any]],
    *,
    raw_records_by_game: Mapping[Any, Mapping[str, Any]] | None = None,
    uncertainty_profile: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    raw_records_by_game = raw_records_by_game or {}
    uncertainty_profile = uncertainty_profile or {}
    now = now or datetime.now(timezone.utc)
    rows = [
        prepare_uncertainty_candidate_row(
            row,
            raw_record=raw_records_by_game.get(row.get("game_pk")),
            uncertainty_profile=uncertainty_profile,
            now=now,
        )
        for row in board_rows
    ]
    rows.sort(
        key=lambda row: (
            row["challenger_label"] != "CANDIDATE",
            -(row["conservative_edge"] if row["conservative_edge"] is not None else -999.0),
            row["game_pk"] if row["game_pk"] is not None else 0,
        )
    )
    return rows


def prepare_uncertainty_candidate_row(
    board_row: Mapping[str, Any],
    *,
    raw_record: Mapping[str, Any] | None = None,
    uncertainty_profile: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    evaluation_mode: Literal["live", "backtest"] = "live",
) -> dict[str, Any]:
    merged = dict(board_row)
    if raw_record:
        merged.update({key: value for key, value in raw_record.items() if value is not None})
    uncertainty_profile = uncertainty_profile or {}
    now = now or datetime.now(timezone.utc)

    model_p = selected_model_probability(merged)
    market_p = selected_market_probability(merged)
    edge = favorite_edge(merged)
    favorite_home = raw_model_favorite_home(merged) if model_p is not None else True
    american = _favorite_american(merged, favorite_home)
    bucket = probability_bucket(model_p)
    profile_row = uncertainty_profile.get(bucket, {}) if bucket is not None else {}
    lower = _probability_or_none(profile_row.get("lower"))
    upper = _probability_or_none(profile_row.get("upper"))
    bucket_n = int(profile_row.get("n") or 0)
    low_sample = bool(profile_row.get("low_sample", bucket_n < MIN_BUCKET_N))
    bucket_win_rate = _probability_or_none(profile_row.get("win_rate"))
    conservative_edge = None if lower is None or market_p is None else lower - market_p
    raw_ev = _safe_ev(model_p, american)
    lower_bound_ev = _safe_ev(lower, american)
    flags = _risk_flags(
        merged,
        now=now,
        model_p=model_p,
        edge=edge,
        conservative_edge=conservative_edge,
        low_sample=low_sample,
        favorite_home=favorite_home,
        evaluation_mode=evaluation_mode,
    )
    qualifies = (
        model_p is not None
        and edge is not None
        and conservative_edge is not None
        and lower_bound_ev is not None
        and model_p >= FAVORITE_MODEL_PROBABILITY_MIN
        and edge >= FAVORITE_EDGE_MIN
        and conservative_edge >= LOWER_BOUND_EDGE_MIN
        and lower_bound_ev > 0.0
        and not any(flag in BLOCKING_FLAGS for flag in flags)
        and "EDGE_DOES_NOT_SURVIVE_UNCERTAINTY" not in flags
    )

    return {
        "game_pk": merged.get("game_pk"),
        "matchup": merged.get("matchup"),
        "first_pitch": merged.get("game_start_pacific") or merged.get("game_start_timestamp"),
        "raw_model_favorite": "HOME" if favorite_home else "AWAY",
        "raw_model_favorite_label": _side_label(merged, favorite_home),
        "model_favorite_probability": model_p,
        "model_wins_per_100": wins_per_100(model_p),
        "bucket": bucket,
        "bucket_n": bucket_n,
        "similar_games_wins_per_100": _similar_games_display(bucket_win_rate, bucket_n, low_sample),
        "uncertainty_lower": lower,
        "uncertainty_upper": upper,
        "uncertainty_range_per_100": _uncertainty_range_display(lower, upper, bucket_n, low_sample),
        "market_probability": market_p,
        "market_wins_per_100": wins_per_100(market_p),
        "favorite_edge": edge,
        "favorite_edge_display": percentage_points(edge),
        "conservative_edge": conservative_edge,
        "conservative_edge_per_100": percentage_points(conservative_edge),
        "offered_odds": _format_american(american),
        "raw_ev": raw_ev,
        "lower_bound_ev": lower_bound_ev,
        "baseline_label": str(merged.get("recommendation") or merged.get("action_label") or "PASS"),
        "challenger_label": "CANDIDATE" if qualifies else "WATCH",
        "risk_flags": flags,
    }


def _risk_flags(
    row: Mapping[str, Any],
    *,
    now: datetime,
    model_p: float | None,
    edge: float | None,
    conservative_edge: float | None,
    low_sample: bool,
    favorite_home: bool,
    evaluation_mode: Literal["live", "backtest"] = "live",
) -> list[str]:
    flags: list[str] = []
    required = ("game_pk", "model_probability", "market_probability", "edge", "model_version")
    if any(row.get(field) is None for field in required):
        flags.append("MISSING_REQUIRED_FIELDS")
    if row.get("home_american") is None or row.get("away_american") is None:
        flags.append("MISSING_ODDS")
    odds_ts = _parse_timestamp(row.get("odds_snapshot_timestamp"))
    stale_clock = now if evaluation_mode == "live" else _row_reference_timestamp(row)
    if odds_ts is not None and stale_clock is not None and stale_clock - odds_ts > timedelta(hours=STALE_ODDS_HOURS):
        flags.append("STALE_ODDS")
    if evaluation_mode == "live":
        game_start = _parse_timestamp(row.get("game_start_timestamp"))
        if game_start is not None:
            if now >= game_start:
                flags.append("GAME_ALREADY_STARTED")
            elif game_start - now <= timedelta(minutes=STARTING_SOON_MINUTES):
                flags.append("GAME_STARTING_SOON")
    if low_sample:
        flags.append("LOW_SAMPLE_UNCERTAINTY")
    if model_p is None or model_p < FAVORITE_MODEL_PROBABILITY_MIN:
        flags.append("MODEL_PROB_NEAR_50")
    if conservative_edge is None or conservative_edge < LOWER_BOUND_EDGE_MIN:
        flags.append("EDGE_DOES_NOT_SURVIVE_UNCERTAINTY")
    if edge is not None and abs(edge) >= LARGE_DISAGREEMENT_EDGE:
        flags.append("LARGE_MODEL_MARKET_DISAGREEMENT")
    baseline_home = _baseline_picked_home(row)
    if _is_play(row) and baseline_home is not None and baseline_home != favorite_home:
        flags.append("BASELINE_PLAY_AGAINST_MODEL_FAVORITE")
    return flags


def build_shadow_strategy_comparison(
    predictions: Sequence[Mapping[str, Any]],
    journal: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare baseline PLAY, raw model favorite, and challenger on resolved journal rows."""
    from app.dashboard_analytics import flat_stake_profit, is_play, prediction_key, resolved_prediction_rows

    resolved = resolved_prediction_rows(predictions, journal)
    resolved_by_pk = {row["game_pk"]: row for row in resolved}
    resolved_keys = {prediction_key(row) for row in resolved}
    latest = _latest_predictions_per_game(predictions)
    baseline_latest = [row for row in latest if is_play(row)]
    model_favorite_latest = list(latest)
    challenger_latest = [
        row
        for row in latest
        if prepare_uncertainty_candidate_row(
            row,
            uncertainty_profile=build_uncertainty_profile(
                predictions,
                journal,
                exclude_game_pk=row.get("game_pk"),
                as_of_timestamp=_row_reference_timestamp(row),
            ),
            evaluation_mode="backtest",
        )["challenger_label"]
        == "CANDIDATE"
    ]

    return {
        "window_label": "Prospective daily.jsonl + journal (one window; latest prediction per game_pk)",
        "note": (
            "Shadow strategy comparison on this prospective daily.jsonl and journal "
            "window only. Challenger Wilson buckets use expanding as-of resolved "
            "history (strictly earlier games), not a current-sample CI. "
            "N is selected rows including pending; averages use resolved rows. "
            "Pending games are excluded from win rate and ROI."
        ),
        "strategies": {
            "baseline_play": _strategy_metrics(baseline_latest, resolved_by_pk, resolved_keys, "baseline_play"),
            "raw_model_favorite": _strategy_metrics(
                model_favorite_latest, resolved_by_pk, resolved_keys, "raw_model_favorite"
            ),
            "uncertainty_challenger": _strategy_metrics(
                challenger_latest, resolved_by_pk, resolved_keys, "uncertainty_challenger"
            ),
        },
    }


def _strategy_metrics(
    selected_latest: Sequence[Mapping[str, Any]],
    resolved_by_pk: Mapping[Any, Mapping[str, Any]],
    resolved_keys: set[tuple[Any, str | None]],
    strategy: str,
) -> dict[str, Any]:
    from app.dashboard_analytics import EDGE_BUCKET_SPECS, flat_stake_profit

    wins = losses = pending = 0
    profits: list[float] = []
    model_ps: list[float] = []
    market_ps: list[float] = []
    edges: list[float] = []
    favorite_count = underdog_count = home_count = away_count = 0
    edge_bucket_counts: dict[str, int] = {label: 0 for label, _, _ in EDGE_BUCKET_SPECS}
    prob_bucket_counts: dict[str, int] = {label: 0 for label, _, _ in BUCKET_SPECS}

    for prediction in selected_latest:
        game_pk = prediction.get("game_pk")
        key = _prediction_key(prediction)
        model_p = selected_model_probability(prediction)
        market_p = selected_market_probability(prediction)
        edge = favorite_edge(prediction)
        if model_p is not None:
            bucket = probability_bucket(model_p)
            if bucket is not None:
                prob_bucket_counts[bucket] = prob_bucket_counts.get(bucket, 0) + 1
        if edge is not None:
            for label, lower, upper in EDGE_BUCKET_SPECS:
                value = abs(edge)
                if upper is None and value >= lower:
                    edge_bucket_counts[label] += 1
                    break
                if upper is not None and lower <= value < upper:
                    edge_bucket_counts[label] += 1
                    break

        favorite_home = raw_model_favorite_home(prediction) if model_p is not None else True
        if strategy == "baseline_play":
            side_home = _baseline_picked_home(prediction)
            if side_home is None:
                continue
            is_favorite_side = side_home == favorite_home
        else:
            side_home = favorite_home
            is_favorite_side = True
        if is_favorite_side:
            favorite_count += 1
        else:
            underdog_count += 1
        if side_home:
            home_count += 1
        else:
            away_count += 1

        if key not in resolved_keys:
            pending += 1
            continue
        resolved_row = resolved_by_pk.get(game_pk)
        if resolved_row is None:
            pending += 1
            continue
        if model_p is not None:
            model_ps.append(model_p)
        if market_p is not None:
            market_ps.append(market_p)
        if edge is not None:
            edges.append(edge)

        if strategy == "baseline_play":
            won = resolved_row.get("correct") is True
            american = resolved_row.get("pick_american")
        else:
            actual_home_win = bool(resolved_row["actual_home_win"])
            won = actual_home_win if favorite_home else not actual_home_win
            american = _favorite_american(prediction, favorite_home)

        if won:
            wins += 1
        else:
            losses += 1
        profit = flat_stake_profit(won=won, american=american if isinstance(american, int) else None)
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
        "favorite_count": favorite_count,
        "underdog_count": underdog_count,
        "home_count": home_count,
        "away_count": away_count,
        "model_probability_buckets": [
            {"bucket": label, "n": prob_bucket_counts.get(label, 0)} for label, _, _ in BUCKET_SPECS
        ],
        "edge_buckets": [
            {"bucket": label, "n": edge_bucket_counts.get(label, 0)} for label, _, _ in EDGE_BUCKET_SPECS
        ],
    }


def _similar_games_display(
    bucket_win_rate: float | None,
    bucket_n: int,
    low_sample: bool,
) -> str:
    if low_sample or bucket_n < MIN_BUCKET_N or bucket_win_rate is None:
        return "unavailable (low sample)"
    return wins_per_100(bucket_win_rate)


def _uncertainty_range_display(
    lower: float | None,
    upper: float | None,
    bucket_n: int,
    low_sample: bool,
) -> str:
    if low_sample or bucket_n < MIN_BUCKET_N:
        return "unavailable (low sample)"
    if lower is not None and upper is not None:
        return f"{wins_per_100(lower)}-{wins_per_100(upper)}"
    return "-"


def _favorite_american(row: Mapping[str, Any], favorite_home: bool) -> int | None:
    american = row.get("home_american") if favorite_home else row.get("away_american")
    return american if isinstance(american, int) and not isinstance(american, bool) else None


def _latest_predictions_per_game(predictions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    latest: dict[Any, Mapping[str, Any]] = {}
    for prediction in predictions:
        game_pk = prediction.get("game_pk")
        current = latest.get(game_pk)
        if current is None or _timestamp_sort_key(prediction.get("prediction_timestamp")) >= _timestamp_sort_key(
            current.get("prediction_timestamp")
        ):
            latest[game_pk] = prediction
    return list(latest.values())


def _latest_journal_by_key(journal: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, str | None], Mapping[str, Any]]:
    latest: dict[tuple[Any, str | None], Mapping[str, Any]] = {}
    for row in journal:
        key = _prediction_key(row)
        current = latest.get(key)
        if current is None or str(row.get("enrichment_timestamp")) >= str(current.get("enrichment_timestamp")):
            latest[key] = row
    return latest


def _prediction_key(row: Mapping[str, Any]) -> tuple[Any, str | None]:
    timestamp = row.get("prediction_timestamp")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    return (row.get("game_pk"), str(timestamp) if timestamp is not None else None)


def _is_play(row: Mapping[str, Any]) -> bool:
    edge = row.get("edge")
    return (
        isinstance(edge, (int, float))
        and not isinstance(edge, bool)
        and abs(float(edge)) >= DEFAULT_EDGE_THRESHOLD
    )


def _baseline_picked_home(row: Mapping[str, Any]) -> bool | None:
    edge = row.get("edge")
    if not isinstance(edge, (int, float)) or isinstance(edge, bool):
        return None
    return float(edge) >= 0.0


def _safe_ev(probability: float | None, american: int | None) -> float | None:
    if probability is None or american is None:
        return None
    try:
        return expected_value(probability, american)
    except ValueError:
        return None


def _side_label(row: Mapping[str, Any], favorite_home: bool) -> str:
    key = "home_team" if favorite_home else "away_team"
    label = row.get(key)
    if label is not None:
        return str(label)
    return "HOME" if favorite_home else "AWAY"


def _format_american(value: int | None) -> str:
    if value is None:
        return "-"
    return f"+{value}" if value > 0 else str(value)


def _probability_or_none(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    if not 0.0 <= value <= 1.0:
        return None
    return value


def _row_reference_timestamp(row: Mapping[str, Any]) -> datetime | None:
    """Prediction time, falling back to first pitch when prediction_timestamp is missing."""
    return _parse_timestamp(row.get("prediction_timestamp")) or _parse_timestamp(
        row.get("game_start_timestamp")
    )


def _resolved_before_as_of(row: Mapping[str, Any], as_of: datetime) -> bool:
    """True when a row's outcome could be known at ``as_of`` for shadow backtest buckets."""
    game_start = _parse_timestamp(row.get("game_start_timestamp"))
    if game_start is not None:
        return game_start < as_of
    prediction_ts = _parse_timestamp(row.get("prediction_timestamp"))
    return prediction_ts is not None and prediction_ts < as_of


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
