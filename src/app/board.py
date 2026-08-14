"""APP-001 - data loading/shaping for the Streamlit daily prediction board.

Reads today's IMMUTABLE PIPE-001 prediction records from any prediction store
exposing ``.records()`` (``InMemoryPredictionStore`` / ``JsonLinesPredictionStore``,
see :mod:`pipelines.daily`) and shapes them into simple display rows for the
Streamlit page in ``app.daily_board_page``.

No probability/edge math happens here. ``model_probability``, ``market_probability``,
and ``edge`` are read verbatim from the PIPE-001 record -- MARKET-001
(``src/market/``) already computed them; this module only selects, labels, and
sorts fields for display.

Pass/play threshold
--------------------
Neither ``src/market/`` nor ``src/pipelines/daily.py`` defines a pass/play
betting threshold -- MARKET-001 stops at edge/EV, and no staking-policy ADR
exists yet. ``DEFAULT_EDGE_THRESHOLD`` is therefore a SYNTHETIC, DISPLAY-ONLY
default (not a business decision, not model output): the board marks a game
"PLAY" when ``abs(edge) >= edge_threshold``, else "PASS". Treat this as a
placeholder UI label, not a recommendation; promote it to a real policy via an
ADR before it is used for anything beyond a dashboard indicator.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import json

# ponytail: MLB's 30 team_ids are a fixed, unchanging set with no existing
# id->name lookup anywhere in the repo (checked ingestion/storage/features).
# A static map is the whole solution; promote to a real teams table only if
# something beyond display ever needs it.
TEAM_ABBREVIATIONS: dict[int, str] = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

DEFAULT_EDGE_THRESHOLD = 0.02
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
DAILY_BOARD_REQUIRED_FIELDS = (
    "game_pk",
    "model_probability",
    "market_probability",
    "edge",
    "odds_snapshot_timestamp",
    "model_version",
)
SKIP_NO_STARTER_ANNOUNCED = "no_starter_announced"
PENDING_STARTER_MESSAGE = (
    "No starting pitcher has been chosen for this game yet. Check back later."
)
DEFAULT_SKIPPED_PATH = Path("state/predictions/skipped.jsonl")


def load_daily_board(
    store: Any,
    *,
    run_date: Any = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
    journal_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Shape PIPE-001 prediction records into display rows, sorted by game_pk.

    Malformed records are skipped rather than crashing the whole board. Use
    :func:`load_daily_board_with_diagnostics` when the caller needs skipped-row
    reasons for display.
    """
    return load_daily_board_with_diagnostics(
        store,
        run_date=run_date,
        edge_threshold=edge_threshold,
        journal_store=journal_store,
    )["rows"]


def load_daily_board_with_diagnostics(
    store: Any,
    *,
    run_date: Any = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
    journal_store: Any | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return display rows plus skipped malformed-record diagnostics."""
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    journal_by_key = _journal_by_prediction_key(journal_store)
    for position, record in enumerate(store.records()):
        if run_date is not None and isinstance(record, Mapping) and record.get("run_date") != run_date:
            continue
        problem = _record_problem(record)
        if problem is not None:
            skipped.append(
                {
                    "position": position,
                    "game_pk": record.get("game_pk") if isinstance(record, Mapping) else None,
                    "run_date": record.get("run_date") if isinstance(record, Mapping) else None,
                    "reason": problem,
                }
            )
            continue
        assert isinstance(record, Mapping)
        edge = float(record["edge"])
        home_team_id = record.get("home_team_id")
        away_team_id = record.get("away_team_id")
        picked_home = edge >= 0
        model_side_id = home_team_id if picked_home else away_team_id
        model_side = _team_label(model_side_id)
        model_probability = float(record["model_probability"])
        market_probability = float(record["market_probability"])
        model_chance = model_probability if picked_home else 1.0 - model_probability
        market_chance = market_probability if picked_home else 1.0 - market_probability
        play = abs(edge) >= edge_threshold
        game_start_pacific = _format_pacific(record.get("game_start_timestamp"))
        prediction_pacific = _format_pacific(record.get("prediction_timestamp"))
        odds_snapshot_pacific = _format_pacific(record["odds_snapshot_timestamp"])
        prediction_ts = _parse_datetime(record.get("prediction_timestamp"))
        journal = journal_by_key.get(
            _prediction_key(record.get("game_pk"), record.get("prediction_timestamp"))
        )
        rows.append(
            {
                "game_pk": record["game_pk"],
                "run_date": record.get("run_date"),
                "matchup": _matchup(away_team_id, home_team_id),
                "home_team": _team_label(home_team_id),
                "away_team": _team_label(away_team_id),
                "pick": model_side,
                "pick_side": "home" if picked_home else "away",
                "model_chance": model_chance,
                "market_chance": market_chance,
                "difference": model_chance - market_chance,
                "recommendation": f"PLAY {model_side}" if play else "PASS",
                "model_side": model_side,
                "model_side_detail": f"{model_side} ({'home' if picked_home else 'away'})",
                "model_probability": record["model_probability"],
                "market_probability": record["market_probability"],
                "edge": edge,
                "game_start_pacific": game_start_pacific,
                "prediction_timestamp": prediction_ts,
                "prediction_timestamp_pacific": prediction_pacific,
                "odds_snapshot_timestamp": record["odds_snapshot_timestamp"],
                "odds_snapshot_pacific": odds_snapshot_pacific,
                "model_version": record["model_version"],
                "play": play,
                "action_label": f"PLAY {model_side}" if play else "PASS",
                "result_status": _result_status(journal),
                "result_label": _result_label(journal),
                "actual_home_win": journal.get("actual_home_win") if journal else None,
                "correct": journal.get("correct") if journal else None,
            }
        )
    rows = _latest_row_per_game(rows)
    rows.sort(key=lambda r: r["game_pk"])
    return {"rows": rows, "skipped": skipped}


def _journal_by_prediction_key(journal_store: Any | None) -> dict[tuple[Any, str | None], Mapping[str, Any]]:
    if journal_store is None:
        return {}
    by_key: dict[tuple[Any, str | None], Mapping[str, Any]] = {}
    for record in journal_store.records():
        if not isinstance(record, Mapping):
            continue
        key = _prediction_key(record.get("game_pk"), record.get("prediction_timestamp"))
        current = by_key.get(key)
        if current is None or str(record.get("enrichment_timestamp")) >= str(current.get("enrichment_timestamp")):
            by_key[key] = record
    return by_key


def _prediction_key(game_pk: Any, prediction_timestamp: Any) -> tuple[Any, str | None]:
    parsed = _parse_datetime(prediction_timestamp)
    if parsed is not None:
        return (game_pk, parsed.isoformat())
    if prediction_timestamp is None:
        return (game_pk, None)
    return (game_pk, str(prediction_timestamp))


def _result_status(journal: Mapping[str, Any] | None) -> str:
    return "Final" if journal else "Pending"


def _result_label(journal: Mapping[str, Any] | None) -> str | None:
    if not journal:
        return None
    home_score = journal.get("home_score")
    away_score = journal.get("away_score")
    if home_score is not None and away_score is not None:
        return f"Final: Home {home_score} - Away {away_score}"
    actual = journal.get("actual_home_win")
    if isinstance(actual, bool):
        return "Final: home won" if actual else "Final: away won"
    return "Final"


def _latest_row_per_game(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest prediction per ``game_pk`` for board display."""
    latest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        game_pk = row["game_pk"]
        current = latest.get(game_pk)
        if current is None:
            latest[game_pk] = row
            continue
        row_ts = row.get("prediction_timestamp")
        current_ts = current.get("prediction_timestamp")
        if isinstance(row_ts, datetime) and isinstance(current_ts, datetime):
            if row_ts >= current_ts:
                latest[game_pk] = row
        elif row_ts is not None:
            latest[game_pk] = row
    return list(latest.values())


def _team_label(team_id: Any) -> str:
    if team_id in TEAM_ABBREVIATIONS:
        return TEAM_ABBREVIATIONS[team_id]
    return f"Team {team_id}"


def _matchup(away_team_id: Any, home_team_id: Any) -> str:
    return f"{_team_label(away_team_id)} @ {_team_label(home_team_id)}"


def available_run_dates(store: Any) -> list[str]:
    """Return sorted run_date values available in a prediction store."""
    dates = {str(record.get("run_date")) for record in store.records() if record.get("run_date") is not None}
    return sorted(dates)


def latest_run_date(store: Any) -> str | None:
    """Return the latest run_date string available in a prediction store."""
    dates = available_run_dates(store)
    return dates[-1] if dates else None


def load_starter_pending_games(
    skipped_path: Path,
    run_date: Any,
) -> list[dict[str, Any]]:
    """Games withheld from prediction because a starter is not announced yet."""
    if not skipped_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in skipped_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if str(record.get("run_date")) != str(run_date):
            continue
        if record.get("reason") != SKIP_NO_STARTER_ANNOUNCED:
            continue
        rows.append(
            {
                "game_pk": record["game_pk"],
                "matchup": _matchup(record.get("away_team_id"), record.get("home_team_id")),
                "game_start_pacific": _format_pacific(record.get("game_start_timestamp")),
                "message": record.get("message", PENDING_STARTER_MESSAGE),
            }
        )
    rows.sort(key=lambda row: row["game_pk"])
    return rows


def _format_pacific(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    local = parsed.astimezone(PACIFIC_TZ)
    # 24-hour time, not 12-hour + AM/PM: Streamlit's dataframe sorts this
    # column as plain text, and "01:05 PM" vs "10:35 AM" sorts as strings
    # ("0" < "1"), not chronologically. Zero-padded 24-hour time sorts
    # correctly as text because it IS chronological order.
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_problem(record: Any) -> str | None:
    if not isinstance(record, Mapping):
        return "record is not an object"
    missing = [field for field in DAILY_BOARD_REQUIRED_FIELDS if record.get(field) is None]
    if missing:
        return "missing required field(s): " + ", ".join(missing)
    for field in ("model_probability", "market_probability", "edge"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{field} must be numeric"
    return None
