"""APP-013 - daily model signal dashboard helpers for the Streamlit home page.

Display-only transformations over board rows and artifact-backed prediction
records. Does not recompute model probability, market probability, or edge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.board import DEFAULT_EDGE_THRESHOLD
from app.dashboard_analytics import (
    build_betting_results_summary,
    build_prospective_model_quality,
    is_play,
    picked_home,
    read_jsonl,
)
from app.game_detail import _group_features, _load_raw_features, _notable_stat_gaps
from app.homepage import MODEL_IDENTITY, ArtifactPaths, build_homepage_summary
from features.build import _COMPONENTS as FEATURE_COMPONENTS
from market.engine import expected_value

SIGNAL_CONTEXT_NOTE = (
    "Model context is directional. These values show what the model saw, but "
    "they are not guaranteed causal reasons."
)
INTERPRETATION_NOTE = (
    "This is a model-market disagreement, not a guaranteed outcome."
)
LARGE_EDGE_THRESHOLD = 0.08
NEAR_FIFTY_LOW = 0.48
NEAR_FIFTY_HIGH = 0.52
LOW_EDGE_THRESHOLD = 0.01
STALE_ODDS_HOURS = 4
STALE_PREDICTION_HOURS = 12
STARTING_SOON_MINUTES = 30

FRONT_EDGE_BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("|edge| < 1 pp", 0.0, 0.01),
    ("1–2 pp", 0.01, 0.02),
    ("2–4 pp", 0.02, 0.04),
    ("4–6 pp", 0.04, 0.06),
    ("6–8 pp", 0.06, 0.08),
    ("8+ pp", 0.08, None),
)

FEATURE_GROUP_LABELS = {
    "team_form": "Team form",
    "starter": "Starter",
    "bullpen": "Bullpen",
    "rest_schedule": "Rest / schedule",
    "home_away": "Home / away context",
    "offense": "Offense",
}


def format_probability(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def format_edge_pp(value: float | None) -> str:
    if value is None:
        return "—"
    pp = value * 100
    sign = "+" if pp >= 0 else ""
    return f"{sign}{pp:.1f} pp"


def format_american_odds(value: int | None) -> str:
    if value is None:
        return "—"
    return f"+{value}" if value > 0 else str(value)


def derive_model_side(row: Mapping[str, Any]) -> str:
    return "HOME" if picked_home(row) else "AWAY"


def derive_selected_side_probability(row: Mapping[str, Any]) -> float | None:
    model_probability = row.get("model_probability")
    if not isinstance(model_probability, (int, float)) or isinstance(model_probability, bool):
        return None
    p_home = float(model_probability)
    return p_home if picked_home(row) else 1.0 - p_home


def derive_selected_side_market_probability(row: Mapping[str, Any]) -> float | None:
    market_probability = row.get("market_probability")
    if not isinstance(market_probability, (int, float)) or isinstance(market_probability, bool):
        return None
    p_home = float(market_probability)
    return p_home if picked_home(row) else 1.0 - p_home


def derive_selected_side_edge(row: Mapping[str, Any]) -> float | None:
    edge = row.get("edge")
    if not isinstance(edge, (int, float)) or isinstance(edge, bool):
        return None
    value = float(edge)
    return value if picked_home(row) else -value


def derive_signal_label(row: Mapping[str, Any]) -> str:
    edge = row.get("edge")
    if not isinstance(edge, (int, float)) or isinstance(edge, bool):
        return "DATA WARNING"
    value = float(edge)
    if abs(value) >= LARGE_EDGE_THRESHOLD:
        return "REVIEW LARGE EDGE"
    if not is_play(row):
        return "NO EDGE"
    return "VALUE ON HOME" if picked_home(row) else "VALUE ON AWAY"


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


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def derive_risk_flags(
    row: Mapping[str, Any],
    *,
    now: datetime | None = None,
    pending_starter_game_pks: set[Any] | None = None,
    features_available: bool = True,
) -> list[str]:
    flags: list[str] = []
    now = now or _now_utc()
    edge = row.get("edge")
    edge_value = float(edge) if isinstance(edge, (int, float)) and not isinstance(edge, bool) else None

    if row.get("home_american") is None or row.get("away_american") is None:
        flags.append("MISSING_ODDS")

    odds_ts = _parse_timestamp(row.get("odds_snapshot_timestamp"))
    if odds_ts is not None and now - odds_ts > timedelta(hours=STALE_ODDS_HOURS):
        flags.append("STALE_ODDS")

    prediction_ts = _parse_timestamp(row.get("prediction_timestamp"))
    if prediction_ts is not None and now - prediction_ts > timedelta(hours=STALE_PREDICTION_HOURS):
        flags.append("STALE_PREDICTION")

    game_start = _parse_timestamp(row.get("game_start_timestamp"))
    if game_start is not None:
        if now >= game_start:
            flags.append("GAME_ALREADY_STARTED")
        elif game_start - now <= timedelta(minutes=STARTING_SOON_MINUTES):
            flags.append("GAME_STARTING_SOON")

    if prediction_ts is not None and game_start is not None and prediction_ts >= game_start:
        flags.append("DATA WARNING")

    selected_p = derive_selected_side_probability(row)
    if selected_p is not None and NEAR_FIFTY_LOW <= selected_p <= NEAR_FIFTY_HIGH:
        flags.append("MODEL_PROB_NEAR_50")

    if edge_value is not None:
        if abs(edge_value) < LOW_EDGE_THRESHOLD:
            flags.append("LOW_EDGE")
        if abs(edge_value) >= LARGE_EDGE_THRESHOLD:
            flags.append("LARGE_MODEL_MARKET_DISAGREEMENT")

    if not features_available:
        flags.append("MISSING_FEATURE_VALUES")

    game_pk = row.get("game_pk")
    if pending_starter_game_pks and game_pk in pending_starter_game_pks:
        flags.append("UNKNOWN_STARTER_RISK")

    required = (
        "game_pk",
        "model_probability",
        "market_probability",
        "edge",
        "model_version",
    )
    if any(row.get(field) is None for field in required):
        flags.append("MISSING_FEATURE_VALUES")

    return flags


def prepare_daily_signal_row(
    board_row: Mapping[str, Any],
    *,
    raw_record: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    pending_starter_game_pks: set[Any] | None = None,
    features_available: bool = True,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(board_row)
    if raw_record:
        for key in (
            "build_id",
            "home_american",
            "away_american",
            "source",
            "game_start_timestamp",
            "prediction_timestamp",
            "odds_snapshot_timestamp",
        ):
            if key in raw_record and raw_record.get(key) is not None:
                merged[key] = raw_record[key]

    side = derive_model_side(merged)
    side_edge = derive_selected_side_edge(merged)
    pick_american = (
        merged.get("home_american") if picked_home(merged) else merged.get("away_american")
    )
    selected_model_p = derive_selected_side_probability(merged)
    ev = None
    if selected_model_p is not None and isinstance(pick_american, int):
        try:
            ev = expected_value(selected_model_p, pick_american)
        except ValueError:
            ev = None

    flags = derive_risk_flags(
        merged,
        now=now,
        pending_starter_game_pks=pending_starter_game_pks,
        features_available=features_available,
    )
    why_summary = _short_why_summary(side, side_edge)

    return {
        "game_pk": merged.get("game_pk"),
        "matchup": merged.get("matchup"),
        "first_pitch": merged.get("game_start_pacific"),
        "model_side": side,
        "model_side_probability": selected_model_p,
        "market_side_probability": derive_selected_side_market_probability(merged),
        "edge_pp": side_edge,
        "edge_pp_display": format_edge_pp(side_edge),
        "model_probability_home": merged.get("model_probability"),
        "market_probability_home": merged.get("market_probability"),
        "raw_edge": merged.get("edge"),
        "sportsbook_odds": format_american_odds(pick_american if isinstance(pick_american, int) else None),
        "odds_snapshot": merged.get("odds_snapshot_pacific")
        or merged.get("odds_snapshot_timestamp"),
        "prediction_timestamp": merged.get("prediction_timestamp_pacific")
        or merged.get("prediction_timestamp"),
        "model_version": merged.get("model_version"),
        "build_id": merged.get("build_id"),
        "signal_label": derive_signal_label(merged),
        "risk_flags": flags,
        "why_summary": why_summary,
        "expected_value": ev,
        "play": is_play(merged),
        "run_date": merged.get("run_date"),
    }


def prepare_daily_signal_table(
    board_rows: Sequence[Mapping[str, Any]],
    *,
    raw_records_by_game: Mapping[Any, Mapping[str, Any]] | None = None,
    features_path: Path | None = None,
    now: datetime | None = None,
    pending_starter_game_pks: set[Any] | None = None,
) -> list[dict[str, Any]]:
    raw_records_by_game = raw_records_by_game or {}
    rows = [
        prepare_daily_signal_row(
            board_row,
            raw_record=raw_records_by_game.get(board_row.get("game_pk")),
            now=now,
            pending_starter_game_pks=pending_starter_game_pks,
            features_available=_features_available(
                features_path, board_row.get("game_pk"), board_row.get("run_date")
            ),
        )
        for board_row in board_rows
    ]
    rows.sort(
        key=lambda row: (
            -abs(row["edge_pp"]) if row["edge_pp"] is not None else 0.0,
            row["game_pk"] if row["game_pk"] is not None else 0,
        )
    )
    return rows


def prepare_edge_buckets(board_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    edges = [
        abs(float(row["edge"]))
        for row in board_rows
        if isinstance(row.get("edge"), (int, float)) and not isinstance(row.get("edge"), bool)
    ]
    buckets: list[dict[str, Any]] = []
    for label, lower, upper in FRONT_EDGE_BUCKET_SPECS:
        members = [value for value in edges if _in_bucket(value, lower, upper)]
        buckets.append(
            {
                "bucket": label,
                "count": len(members),
                "average_abs_edge_pp": format_edge_pp(float(sum(members) / len(members)))
                if members
                else "—",
            }
        )
    return buckets


def prepare_selected_game_detail(
    board_row: Mapping[str, Any],
    *,
    raw_record: Mapping[str, Any] | None = None,
    features_path: Path | None = None,
    now: datetime | None = None,
    pending_starter_game_pks: set[Any] | None = None,
) -> dict[str, Any]:
    signal_row = prepare_daily_signal_row(
        board_row,
        raw_record=raw_record,
        now=now,
        pending_starter_game_pks=pending_starter_game_pks,
        features_available=_features_available(
            features_path, board_row.get("game_pk"), board_row.get("run_date")
        ),
    )
    side_edge = signal_row["edge_pp"]
    if side_edge is None:
        interpretation = "Insufficient data to compare model and market."
    elif abs(side_edge) < LOW_EDGE_THRESHOLD:
        interpretation = "The model and market mostly agree. No meaningful edge is currently detected."
    else:
        interpretation = (
            f"The model favors the {signal_row['model_side'].lower()} side more than the "
            f"market by {format_edge_pp(side_edge)}. {INTERPRETATION_NOTE}"
        )

    feature_groups = prepare_feature_context(
        features_path,
        signal_row["game_pk"],
        board_row.get("run_date"),
        home_team=board_row.get("home_team"),
        away_team=board_row.get("away_team"),
    )

    merged = dict(board_row)
    if raw_record:
        merged.update(raw_record)

    return {
        **signal_row,
        "interpretation": interpretation,
        "feature_context_note": SIGNAL_CONTEXT_NOTE,
        "feature_groups": feature_groups,
        "home_odds": format_american_odds(merged.get("home_american")),
        "away_odds": format_american_odds(merged.get("away_american")),
        "sportsbook_source": merged.get("source"),
        "schema_version": merged.get("build_id"),
    }


def prepare_feature_context(
    features_path: Path | None,
    game_pk: Any,
    run_date: Any,
    *,
    home_team: Any = None,
    away_team: Any = None,
) -> list[dict[str, Any]]:
    if features_path is None or not features_path.exists():
        return []
    raw = _load_raw_features(features_path, game_pk, run_date)
    if not raw:
        return []
    grouped = _group_features(raw)
    rows: list[dict[str, Any]] = []
    for component in FEATURE_COMPONENTS:
        component_features = grouped.get(component, {})
        summary = _summarize_feature_group(component_features)
        if summary is None:
            continue
        rows.append(
            {
                "feature_group": FEATURE_GROUP_LABELS.get(component, component.replace("_", " ").title()),
                "home": summary["home"],
                "away": summary["away"],
                "advantage": summary["advantage"],
            }
        )
    if not rows and home_team and away_team:
        gaps = _notable_stat_gaps(raw, str(home_team), str(away_team))
        for gap in gaps[:5]:
            rows.append(
                {
                    "feature_group": gap["label"],
                    "home": gap["home_value"],
                    "away": gap["away_value"],
                    "advantage": gap["home_team"] if gap["diff"] >= 0 else gap["away_team"],
                }
            )
    return rows


def build_signal_dashboard(
    paths: ArtifactPaths | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    paths = paths or ArtifactPaths()
    now = now or _now_utc()
    summary = build_homepage_summary(paths)
    predictions = read_jsonl(paths.predictions)
    journal = read_jsonl(paths.journal)
    prospective = build_prospective_model_quality(predictions, journal)

    from app.board import load_daily_board, load_starter_pending_games
    from observability.journal import JsonLinesJournalStore
    from pipelines.daily import JsonLinesPredictionStore

    board_rows: list[dict[str, Any]] = []
    raw_by_game: dict[Any, dict[str, Any]] = {}
    pending_starter_pks: set[Any] = set()
    run_date = summary["latest_run_date"]

    if paths.predictions.exists() and run_date is not None:
        store = JsonLinesPredictionStore(paths.predictions)
        journal_store = (
            JsonLinesJournalStore(paths.journal) if paths.journal.exists() else None
        )
        board_rows = load_daily_board(
            store, run_date=run_date, journal_store=journal_store
        )
        pending = load_starter_pending_games(paths.skipped, run_date)
        pending_starter_pks = {row["game_pk"] for row in pending}
        board_rows = [row for row in board_rows if row["game_pk"] not in pending_starter_pks]
        for record in store.records():
            if str(record.get("run_date")) != str(run_date):
                continue
            game_pk = record.get("game_pk")
            current = raw_by_game.get(game_pk)
            if current is None or str(record.get("prediction_timestamp")) >= str(
                current.get("prediction_timestamp")
            ):
                raw_by_game[game_pk] = record

    signal_table = prepare_daily_signal_table(
        board_rows,
        raw_records_by_game=raw_by_game,
        features_path=Path("state/predictions/game_features.jsonl"),
        now=now,
        pending_starter_game_pks=pending_starter_pks,
    )
    edges = [
        float(row["raw_edge"])
        for row in board_rows
        if isinstance(row.get("edge"), (int, float)) and not isinstance(row.get("edge"), bool)
    ]
    home_edges = [value for value in edges if value > 0]
    away_edges = [value for value in edges if value < 0]
    with_odds = sum(
        1
        for row in raw_by_game.values()
        if row.get("home_american") is not None and row.get("away_american") is not None
    )
    missing_odds = max(len(board_rows) - with_odds, 0)

    betting = build_betting_results_summary(predictions, journal)

    freshness_warnings: list[str] = []
    if summary["missing_artifacts"]:
        freshness_warnings.append("Missing prediction artifacts")
    if missing_odds:
        freshness_warnings.append(f"{missing_odds} game(s) missing odds")
    if summary["skipped_count"]:
        freshness_warnings.append(f"{summary['skipped_count']} game(s) awaiting data")

    return {
        "model_identity": MODEL_IDENTITY,
        "latest_run_date": run_date,
        "predictions_last_updated": summary["predictions_last_updated"],
        "odds_last_updated": summary["odds_last_updated"],
        "results_last_refreshed": summary["results_last_refreshed"],
        "freshness_warnings": freshness_warnings,
        "system_status": {
            "games_today": len(board_rows),
            "predictions_generated": len(board_rows),
            "games_with_odds": with_odds,
            "missing_odds": missing_odds,
            "model_version": next(iter({row.get("model_version") for row in board_rows if row.get("model_version")}), "—"),
            "build_id": next(iter({raw_by_game[pk].get("build_id") for pk in raw_by_game if raw_by_game[pk].get("build_id")}), "—"),
            "data_quality": "Warning" if freshness_warnings else "OK",
        },
        "signal_summary": {
            "games": len(board_rows),
            "predictions": len(board_rows),
            "with_odds": with_odds,
            "missing_odds": missing_odds,
            "signals_above_threshold": sum(1 for row in signal_table if row["play"]),
            "largest_home_edge_pp": format_edge_pp(max(home_edges) if home_edges else None),
            "largest_away_edge_pp": format_edge_pp(-min(away_edges) if away_edges else None),
            "average_abs_edge_pp": format_edge_pp(
                sum(abs(value) for value in edges) / len(edges) if edges else None
            ),
        },
        "signal_table": signal_table,
        "edge_buckets": prepare_edge_buckets(board_rows),
        "board_rows_by_game": {row["game_pk"]: row for row in board_rows},
        "raw_by_game": raw_by_game,
        "pending_starter_pks": pending_starter_pks,
        "holdout_metrics": summary["holdout_metrics"],
        "prospective": prospective,
        "finished_play_results": {
            "window_label": (
                "Latest prediction per game across all stored slates "
                "(resolved PLAY rows only)."
            ),
            "note": betting["note"],
            "wins": betting["wins"],
            "losses": betting["losses"],
            "pending": betting["pending"],
            "win_rate": betting["win_rate"],
            "average_edge_pp": format_edge_pp(betting["average_edge"]),
            "roi": betting["roi"],
            "units": betting["units"],
            "play_count": betting["play_count"],
        },
        "methodology_label": summary["methodology_label"],
    }


def _short_why_summary(side: str, side_edge: float | None) -> str:
    if side_edge is None:
        return "Insufficient model/market data."
    if abs(side_edge) < LOW_EDGE_THRESHOLD:
        return "Model and market mostly agree."
    return f"Model leans {side} by {format_edge_pp(side_edge)} vs market."


def _features_available(features_path: Path | None, game_pk: Any, run_date: Any) -> bool:
    if features_path is None:
        return False
    return _load_raw_features(features_path, game_pk, run_date) is not None


def _summarize_feature_group(features: Mapping[str, Any]) -> dict[str, Any] | None:
    diffs = [
        (key, value)
        for key, value in features.items()
        if key.startswith("diff_") and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not diffs:
        return None
    key, diff_value = max(diffs, key=lambda item: abs(float(item[1])))
    suffix = key[len("diff_") :]
    home_value = features.get(f"home_{suffix}")
    away_value = features.get(f"away_{suffix}")
    if home_value is None or away_value is None:
        return None
    advantage = "Home" if float(diff_value) >= 0 else "Away"
    return {"home": home_value, "away": away_value, "advantage": advantage}


def _in_bucket(value: float, lower: float, upper: float | None) -> bool:
    if upper is None:
        return value >= lower
    return lower <= value < upper
