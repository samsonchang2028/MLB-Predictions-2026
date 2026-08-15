"""Local operator for real daily MLB moneyline predictions.

This is the concrete runner around PIPE-001. It uses local certified Silver data,
fits the ADR-006 locked V1 model on 2021-2025 only, fetches live The Odds API
moneylines, maps them to today's MLB ``game_pk`` values, and appends immutable
prediction records to ``state/predictions/daily.jsonl``.

The script intentionally does not train on 2026 completed games. ADR-006 locked
V1 methodology was selected on the repaired 2021-2025 certified build; changing
that training policy is a new methodology decision, not an operator default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402

from evaluation.holdout import ADR_006_PATH, LOCKED_PARAMS, require_locked_methodology  # noqa: E402
from evaluation.runner import _vectorize  # noqa: E402
from features.build import build_feature_matrix  # noqa: E402
from features.bullpen import build_bullpen_features  # noqa: E402
from features.starter import build_starter_features  # noqa: E402
from features.team import build_team_features  # noqa: E402
from ingestion.mlb import backfill_game_details, invalidate_game_detail_payloads  # noqa: E402
from ingestion.mlb.statsapi_fetchers import make_game_detail_fetcher  # noqa: E402
from ingestion.odds import (  # noqa: E402
    OddsDataError,
    parse_the_odds_api_moneylines,
    parse_the_odds_api_totals,
)
from market import MarketLabel  # noqa: E402
from storage import connect_database  # noqa: E402
from transforms import normalize_silver  # noqa: E402
from models import xgboost_model  # noqa: E402
from pipelines.daily import JsonLinesPredictionStore, run_daily_predictions  # noqa: E402
from pipelines.daily import _serialize_record as _serialize_detail_record  # noqa: E402
from rerun_repaired_experiment import _build_matrix as _build_training_matrix  # noqa: E402
from rerun_repaired_experiment import _load_inputs as _load_training_inputs  # noqa: E402
from simulation.game_level import SimulationConfig, simulate_games  # noqa: E402
from simulation.markets import totals_probabilities_from_trials  # noqa: E402
from simulation.score_model import fit_score_model  # noqa: E402

DEFAULT_CERTIFICATION = (
    Path("state")
    / "data-certifications"
    / "certification-PASS-a910017bac839af5.json"
)
DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_OUTPUT = Path("state") / "predictions" / "daily.jsonl"
DEFAULT_FEATURES_OUTPUT = Path("state") / "predictions" / "game_features.jsonl"
DEFAULT_ODDS_BOOKS_OUTPUT = Path("state") / "predictions" / "odds_books.jsonl"
DEFAULT_SKIPPED_OUTPUT = Path("state") / "predictions" / "skipped.jsonl"
DEFAULT_SIMULATION_OUTPUT = Path("state") / "predictions" / "simulation.jsonl"
DEFAULT_MODEL_VERSION = "v1-adr-006-tuned-xgboost-expanding-uncalibrated"
DEFAULT_SIMULATION_MODEL_VERSION = "sim-game-level-v1"
DEFAULT_SIMULATION_TRIALS = 10_000
SKIP_NO_STARTER_ANNOUNCED = "no_starter_announced"
PENDING_STARTER_MESSAGE = (
    "No starting pitcher has been chosen for this game yet. Check back later."
)
DEFAULT_BOOKMAKER = "draftkings"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
ODDS_MATCH_TOLERANCE_SECONDS = 12 * 60 * 60


def _format_duration(seconds: float) -> str:
    """Human-readable elapsed time for operator logs."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class _StepTimer:
    """Log per-step and total wall time for long operator runs."""

    def __init__(self) -> None:
        self._run_started = time.monotonic()
        self._step_started = self._run_started

    def log(self, step: str) -> None:
        now = time.monotonic()
        print(
            f"[timing] {step} took {_format_duration(now - self._step_started)}",
            flush=True,
        )
        self._step_started = now

    def log_total(self) -> None:
        print(
            f"[timing] total took {_format_duration(time.monotonic() - self._run_started)}",
            flush=True,
        )


def _dict_rows(connection: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, list(params))
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def parse_date(value: str | None) -> date:
    if value is None:
        return datetime.now().date()
    return date.fromisoformat(value)


def parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--prediction-timestamp must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _runs_by_game_pk(team_stats: Sequence[Mapping[str, Any]]) -> dict[Any, tuple[int, int]]:
    """Map each ``game_pk`` to finalized ``(home_runs, away_runs)`` scores."""
    by_game: dict[Any, dict[str, int]] = {}
    for row in team_stats:
        score = row.get("score")
        if score is None or isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        side = row.get("side")
        if side not in ("home", "away"):
            continue
        by_game.setdefault(row["game_pk"], {})[side] = int(score)
    return {
        game_pk: (sides["home"], sides["away"])
        for game_pk, sides in by_game.items()
        if "home" in sides and "away" in sides
    }


def score_training_rows_from_matrix(
    matrix: Mapping[str, Any],
    team_stats: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Convert historical Gold rows into score-model training examples."""
    runs_index = _runs_by_game_pk(team_stats)
    training_rows: list[dict[str, Any]] = []
    for row in matrix["rows"]:
        runs = runs_index.get(row["game_pk"])
        if runs is None:
            continue
        home_runs, away_runs = runs
        training_rows.append(
            {
                "features": row["features"],
                "home_runs": home_runs,
                "away_runs": away_runs,
            }
        )
    return training_rows


def train_locked_score_model(
    database: str,
    certification_path: Path,
    random_state: int,
) -> tuple[Any, str, int]:
    """Fit the full-Gold Poisson score model on repaired 2021-2025 rows only."""
    inputs = _load_training_inputs(database, certification_path)
    matrix, _counts = _build_training_matrix(inputs)
    if matrix["feature_completeness"]["status"] != "PASS":
        raise RuntimeError("historical Gold completeness is not PASS; refusing to fit score model")
    training_rows = score_training_rows_from_matrix(matrix, inputs["team_stats"])
    if not training_rows:
        raise RuntimeError("score model training matrix has no labeled rows")
    score_model = fit_score_model(
        training_rows,
        feature_columns=matrix["feature_columns"],
        random_state=random_state,
    )
    return score_model, matrix["build_id"], len(training_rows)


def build_simulation_records(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    score_model: Any,
    run_date: date,
    build_id: str,
    n_trials: int = DEFAULT_SIMULATION_TRIALS,
    random_state: int = 0,
    model_version: str = DEFAULT_SIMULATION_MODEL_VERSION,
    totals_lines: Mapping[Any, float] | None = None,
) -> list[dict[str, Any]]:
    """Run Monte Carlo trials for today's Gold rows and shape operator artifacts."""
    config = SimulationConfig(
        n_trials=n_trials,
        random_state=random_state,
        store_trials=True,
    )
    results = simulate_games(feature_rows, score_model=score_model, config=config)
    records: list[dict[str, Any]] = []
    lines = totals_lines or {}
    for result in results:
        if result.home_runs_trials is None or result.away_runs_trials is None:
            raise RuntimeError("simulation trials were not stored")
        totals = np.asarray(result.home_runs_trials, dtype=float) + np.asarray(
            result.away_runs_trials, dtype=float
        )
        record: dict[str, Any] = {
            "run_date": str(run_date),
            "game_pk": result.game_pk,
            "p_home_win": result.p_home_win,
            "home_runs_mean": result.home_runs_mean,
            "away_runs_mean": result.away_runs_mean,
            "total_runs_mean": result.total_runs_mean,
            "total_runs_median": float(np.median(totals)),
            "n_trials": n_trials,
            "model_version": model_version,
            "build_id": build_id,
        }
        line = lines.get(result.game_pk)
        if line is not None:
            p_over, p_under = totals_probabilities_from_trials(
                result.home_runs_trials,
                result.away_runs_trials,
                line=line,
            )
            record["totals_line"] = line
            record["p_over"] = p_over
            record["p_under"] = p_under
        records.append(record)
    return records


def train_locked_model(database: str, certification_path: Path, random_state: int) -> tuple[Any, list[str], str, int]:
    """Fit ADR-006 locked XGBoost on the repaired 2021-2025 training matrix."""
    require_locked_methodology(ADR_006_PATH)
    inputs = _load_training_inputs(database, certification_path)
    matrix, _counts = _build_training_matrix(inputs)
    if matrix["feature_completeness"]["status"] != "PASS":
        raise RuntimeError("historical Gold completeness is not PASS; refusing to fit")
    X, y, _seasons, feature_columns, _game_pks = _vectorize(matrix)
    mask = ~np.isnan(y)
    if not int(mask.sum()):
        raise RuntimeError("training matrix has no labeled rows")
    estimator = xgboost_model.build_model(random_state=random_state, **LOCKED_PARAMS)
    estimator.fit(X[mask], y[mask].astype(int))
    return estimator, feature_columns, matrix["build_id"], int(mask.sum())


def _storage_root_from_database(database: str | Path) -> Path:
    return Path(database).parent


def slate_game_pks_for_detail_refresh(database: str | Path, run_date: date) -> list[int]:
    """Return today's Preview/Live regular-season ``game_pk`` values for detail refresh."""
    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT game_pk
            FROM silver.games
            WHERE official_date = ?
              AND game_type = 'R'
              AND abstract_game_state IN ('Preview', 'Live')
            ORDER BY game_pk
            """,
            [run_date],
        ).fetchall()
    return [int(row[0]) for row in rows]


def refresh_pregame_game_details(
    database: str | Path,
    run_date: date,
    game_detail_fetcher: Any,
) -> dict[str, Any]:
    """Invalidate, re-fetch, and normalize MLB game detail for today's active slate."""
    storage_root = _storage_root_from_database(database)
    game_pks = slate_game_pks_for_detail_refresh(database, run_date)
    if not game_pks:
        return {
            "targeted": 0,
            "invalidated": 0,
            "fetched": 0,
            "normalized": False,
        }

    invalidated = invalidate_game_detail_payloads(storage_root, game_pks)
    backfill = backfill_game_details(
        storage_root,
        game_detail_fetcher,
        game_pks=game_pks,
        retry_unresolved=True,
    )
    with connect_database(database) as connection:
        normalize_silver(connection)

    return {
        "targeted": len(game_pks),
        "invalidated": invalidated,
        "fetched": int(backfill.get("fetched", 0)),
        "normalized": True,
        "game_pks": game_pks,
        "backfill": backfill,
    }


def load_prediction_inputs(database: str, run_date: date, certification_path: Path) -> dict[str, Any]:
    certification = json.loads(certification_path.read_text(encoding="utf-8"))
    with duckdb.connect(database, read_only=True) as connection:
        schedule = _dict_rows(
            connection,
            """
            SELECT game_pk, season, game_type, game_date, game_date AS game_start_timestamp,
                   official_date, home_team_id, away_team_id, game_number,
                   abstract_game_state, detailed_state, source_game_json
            FROM silver.games
            WHERE official_date = ?
              AND game_type = 'R'
              AND abstract_game_state IN ('Preview', 'Live')
            ORDER BY game_date, game_pk
            """,
            [run_date],
        )
        for row in schedule:
            row["game_start_timestamp"] = _utc_instant(row["game_start_timestamp"])
        games_for_features = _dict_rows(
            connection,
            """
            SELECT game_pk, season, game_type, game_date, home_team_id,
                   away_team_id, game_number
            FROM silver.games
            WHERE game_type = 'R'
              AND (
                    season IN ('2021', '2022', '2023', '2024', '2025')
                    OR (season = '2026' AND official_date <= ?)
                  )
            ORDER BY game_date, game_pk
            """,
            [run_date],
        )
        team_stats = _dict_rows(
            connection,
            """
            SELECT t.game_pk, t.team_id, t.side, t.score, t.is_winner,
                   t.game_date, g.season
            FROM silver.team_game_statistics t
            JOIN silver.games g USING (game_pk)
            WHERE g.game_type = 'R'
              AND (
                    g.season IN ('2021', '2022', '2023', '2024', '2025')
                    OR (g.season = '2026' AND g.official_date <= ?)
                  )
            ORDER BY t.game_pk, t.team_id
            """,
            [run_date],
        )
        appearances = _dict_rows(
            connection,
            """
            SELECT p.game_pk, p.team_id, p.side, g.game_date, g.game_type,
                   g.game_number, p.pitcher_id, p.appearance_order,
                   p.is_actual_starter, p.outs_recorded, p.batters_faced,
                   p.pitches_thrown, p.earned_runs, p.hits_allowed, p.walks,
                   p.strikeouts, p.home_runs_allowed
            FROM silver.pitcher_appearances p
            JOIN silver.games g USING (game_pk)
            WHERE g.game_type = 'R'
              AND (
                    g.season IN ('2021', '2022', '2023', '2024', '2025')
                    OR (g.season = '2026' AND g.official_date < ?)
                  )
            ORDER BY g.game_date, p.game_pk, p.team_id, p.appearance_order
            """,
            [run_date],
        )
        starters = _dict_rows(
            connection,
            """
            SELECT ps.game_pk, ps.team_id, ps.side, ps.actual_pitcher_id,
                   ps.probable_pitcher_id
            FROM silver.pitcher_starters ps
            JOIN silver.games g USING (game_pk)
            WHERE g.game_type = 'R'
              AND (
                    g.season IN ('2021', '2022', '2023', '2024', '2025')
                    OR (g.season = '2026' AND g.official_date <= ?)
                  )
            ORDER BY ps.game_pk, ps.team_id
            """,
            [run_date],
        )
    schedule_game_pks = {row["game_pk"] for row in schedule}
    games_for_today = [row for row in games_for_features if row["game_pk"] in schedule_game_pks]
    return {
        "certification": certification,
        "schedule": schedule,
        "games_for_features": games_for_features,
        "games_for_today": games_for_today,
        "team_stats": team_stats,
        "appearances": appearances,
        "starters": starters,
    }


def _starter_is_announced(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    return (
        row.get("actual_pitcher_id") is not None
        or row.get("probable_pitcher_id") is not None
    )


def partition_schedule_by_announced_starters(
    schedule: Sequence[Mapping[str, Any]],
    starters: Sequence[Mapping[str, Any]],
    *,
    run_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split today's slate into predict-ready games and starter-pending games."""
    by_key = {(row["game_pk"], row["team_id"]): row for row in starters}
    ready: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for game in schedule:
        missing_team_ids = [
            game[team_key]
            for team_key in ("home_team_id", "away_team_id")
            if not _starter_is_announced(by_key.get((game["game_pk"], game[team_key])))
        ]
        if missing_team_ids:
            skipped.append(
                {
                    "run_date": str(run_date),
                    "game_pk": game["game_pk"],
                    "reason": SKIP_NO_STARTER_ANNOUNCED,
                    "home_team_id": game["home_team_id"],
                    "away_team_id": game["away_team_id"],
                    "missing_team_ids": missing_team_ids,
                    "game_start_timestamp": game.get("game_start_timestamp"),
                    "message": PENDING_STARTER_MESSAGE,
                }
            )
        else:
            ready.append(dict(game))
    ready.sort(key=lambda row: row["game_pk"])
    skipped.sort(key=lambda row: row["game_pk"])
    return ready, skipped


def replace_skipped_for_run_date(
    path: Path,
    run_date: date,
    skipped: Sequence[Mapping[str, Any]],
) -> int:
    """Rewrite starter-pending rows for one slate date; preserve other dates."""
    run_date_str = str(run_date)
    retained: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if str(row.get("run_date")) == run_date_str:
                continue
            retained.append(row)

    serials = [_serialize_detail_record(row) for row in skipped]
    retained.extend(serials)
    retained.sort(key=lambda row: (str(row.get("run_date")), row.get("game_pk")))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in retained:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(serials)


def build_today_feature_components(inputs: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    team_features = build_team_features(inputs["team_stats"])
    starter_rows = list(inputs["starters"]) + _starter_placeholder_rows(
        inputs["schedule"], inputs["starters"]
    )
    starter_features = build_starter_features(
        inputs["appearances"], inputs["games_for_features"], starter_rows
    )
    bullpen_rows = list(inputs["appearances"]) + _bullpen_placeholder_rows(inputs["schedule"])
    bullpen_features = build_bullpen_features(bullpen_rows)
    return {
        "team": team_features,
        "starter": starter_features,
        "bullpen": bullpen_features,
    }


def _starter_placeholder_rows(
    schedule: Sequence[Mapping[str, Any]],
    starters: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Add explicit unknown-starter rows for live inference gaps.

    Historical builds should remain strict, but pregame live slates often have
    one or both probable starters unavailable. These placeholders do not invent
    identity or stats; they only allow ``build_starter_features`` to emit its
    existing unknown-starter feature row for that team-game.
    """
    existing = {(row["game_pk"], row["team_id"]) for row in starters}
    placeholders: list[dict[str, Any]] = []
    for game in schedule:
        for side, team_key in (("home", "home_team_id"), ("away", "away_team_id")):
            key = (game["game_pk"], game[team_key])
            if key in existing:
                continue
            placeholders.append(
                {
                    "game_pk": game["game_pk"],
                    "team_id": game[team_key],
                    "side": side,
                    "actual_pitcher_id": None,
                    "probable_pitcher_id": None,
                }
            )
    return placeholders


def _bullpen_placeholder_rows(schedule: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Create zero-current-line team-game placeholders for unplayed games.

    ``build_bullpen_features`` emits one feature row per team-game seen in pitcher
    appearances. Pregame games correctly have no appearances yet, so we add one
    synthetic starter row per side. It creates the team-game shell but contributes
    no bullpen line to the current game's own feature row.
    """
    placeholders: list[dict[str, Any]] = []
    for game in schedule:
        for side, team_key in (("home", "home_team_id"), ("away", "away_team_id")):
            placeholders.append(
                {
                    "game_pk": game["game_pk"],
                    "team_id": game[team_key],
                    "side": side,
                    "game_date": game["game_date"],
                    "game_type": "R",
                    "game_number": game.get("game_number"),
                    "pitcher_id": -int(game["game_pk"]) * 10 + (0 if side == "home" else -1),
                    "appearance_order": 1,
                    "is_actual_starter": True,
                    "outs_recorded": None,
                    "batters_faced": None,
                    "pitches_thrown": None,
                    "earned_runs": None,
                    "hits_allowed": None,
                    "walks": None,
                    "strikeouts": None,
                    "home_runs_allowed": None,
                }
            )
    return placeholders


def fetch_odds_payload(api_key: str, *, regions: str, bookmakers: str | None, timeout: float) -> Any:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    url = ODDS_API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "mlb-predictions-local-operator/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"The Odds API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"The Odds API request failed: {exc.reason}") from exc


def odds_snapshots_for_schedule(
    payload: Any,
    schedule: Sequence[Mapping[str, Any]],
    *,
    bookmaker: str,
    source: str = "the_odds_api",
) -> tuple[dict[Any, dict[str, Any]], dict[str, int]]:
    parsed = parse_the_odds_api_moneylines(payload, source=source)
    matchup_index = _schedule_matchup_index(schedule)
    grouped: dict[tuple[str, str, datetime, str, str], dict[str, Any]] = {}
    for row in parsed:
        if row["bookmaker"] != bookmaker:
            continue
        key = (
            row["source_event_id"],
            row["bookmaker"],
            row["commence_time"],
            _normalize_team_name(row["home_team"]),
            _normalize_team_name(row["away_team"]),
        )
        group = grouped.setdefault(
            key,
            {
                "source_event_id": row["source_event_id"],
                "bookmaker": row["bookmaker"],
                "commence_time": row["commence_time"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "snapshot_timestamp": row["snapshot_timestamp"],
            },
        )
        if row["snapshot_timestamp"] > group["snapshot_timestamp"]:
            group["snapshot_timestamp"] = row["snapshot_timestamp"]
        if row["outcome"] == "home":
            group["home_american"] = row["american_price"]
        else:
            group["away_american"] = row["american_price"]

    snapshots: dict[Any, dict[str, Any]] = {}
    stats = Counter()
    for group in grouped.values():
        if "home_american" not in group or "away_american" not in group:
            stats["incomplete_bookmaker_markets"] += 1
            continue
        game, reason = _match_schedule_game(group, matchup_index)
        if game is None:
            stats[f"unmatched_events.{reason}"] += 1
            continue
        game_pk = game["game_pk"]
        current = snapshots.get(game_pk)
        candidate = {
            "home_american": group["home_american"],
            "away_american": group["away_american"],
            "snapshot_timestamp": group["snapshot_timestamp"],
            "source": f"{source}:{bookmaker}",
            "label": MarketLabel.SNAPSHOT,
        }
        if current is None or candidate["snapshot_timestamp"] > current["snapshot_timestamp"]:
            snapshots[game_pk] = candidate
            stats["matched_events"] += 1
    stats["parsed_moneyline_rows"] = len(parsed)
    stats["mapped_games"] = len(snapshots)
    return snapshots, dict(stats)


def all_book_snapshots_for_schedule(
    payload: Any,
    schedule: Sequence[Mapping[str, Any]],
    *,
    source: str = "the_odds_api",
) -> tuple[dict[tuple[Any, str], dict[str, Any]], dict[str, int]]:
    """Map every bookmaker's moneyline to ``game_pk``, keyed by ``(game_pk, bookmaker)``.

    Display/comparison only -- unlike :func:`odds_snapshots_for_schedule`, this
    keeps every book instead of filtering to one, and is never used to build the
    canonical immutable prediction record.
    """
    parsed = parse_the_odds_api_moneylines(payload, source=source)
    matchup_index = _schedule_matchup_index(schedule)
    grouped: dict[tuple[str, str, datetime, str, str], dict[str, Any]] = {}
    for row in parsed:
        key = (
            row["source_event_id"],
            row["bookmaker"],
            row["commence_time"],
            _normalize_team_name(row["home_team"]),
            _normalize_team_name(row["away_team"]),
        )
        group = grouped.setdefault(
            key,
            {
                "bookmaker": row["bookmaker"],
                "commence_time": row["commence_time"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "snapshot_timestamp": row["snapshot_timestamp"],
            },
        )
        if row["snapshot_timestamp"] > group["snapshot_timestamp"]:
            group["snapshot_timestamp"] = row["snapshot_timestamp"]
        if row["outcome"] == "home":
            group["home_american"] = row["american_price"]
        else:
            group["away_american"] = row["american_price"]

    snapshots: dict[tuple[Any, str], dict[str, Any]] = {}
    stats = Counter()
    for group in grouped.values():
        if "home_american" not in group or "away_american" not in group:
            stats["incomplete_bookmaker_markets"] += 1
            continue
        game, reason = _match_schedule_game(group, matchup_index)
        if game is None:
            stats[f"unmatched_events.{reason}"] += 1
            continue
        key = (game["game_pk"], group["bookmaker"])
        current = snapshots.get(key)
        candidate = {
            "game_pk": game["game_pk"],
            "bookmaker": group["bookmaker"],
            "home_american": group["home_american"],
            "away_american": group["away_american"],
            "snapshot_timestamp": group["snapshot_timestamp"],
            "source": source,
        }
        if current is None or candidate["snapshot_timestamp"] > current["snapshot_timestamp"]:
            snapshots[key] = candidate
            stats["matched_events"] += 1
    stats["parsed_moneyline_rows"] = len(parsed)
    stats["mapped_game_books"] = len(snapshots)
    return snapshots, dict(stats)


def totals_lines_for_schedule(
    payload: Any,
    schedule: Sequence[Mapping[str, Any]],
    *,
    bookmaker: str,
    source: str = "the_odds_api",
) -> tuple[dict[Any, float], dict[str, int]]:
    """Map the primary bookmaker's totals line to each ``game_pk`` on the slate."""
    parsed = parse_the_odds_api_totals(payload, source=source)
    matchup_index = _schedule_matchup_index(schedule)
    line_groups: dict[tuple[str, str, datetime, str, str, float], dict[str, Any]] = {}
    for row in parsed:
        if row["bookmaker"] != bookmaker:
            continue
        key = (
            row["source_event_id"],
            row["bookmaker"],
            row["commence_time"],
            _normalize_team_name(row["home_team"]),
            _normalize_team_name(row["away_team"]),
            row["point"],
        )
        group = line_groups.setdefault(
            key,
            {
                "source_event_id": row["source_event_id"],
                "bookmaker": row["bookmaker"],
                "commence_time": row["commence_time"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "point": row["point"],
                "snapshot_timestamp": row["snapshot_timestamp"],
            },
        )
        if row["snapshot_timestamp"] > group["snapshot_timestamp"]:
            group["snapshot_timestamp"] = row["snapshot_timestamp"]

    event_lines: dict[tuple[str, str, datetime, str, str], list[dict[str, Any]]] = {}
    for group in line_groups.values():
        event_key = (
            group["source_event_id"],
            group["bookmaker"],
            group["commence_time"],
            _normalize_team_name(str(group["home_team"])),
            _normalize_team_name(str(group["away_team"])),
        )
        event_lines.setdefault(event_key, []).append(group)

    lines: dict[Any, float] = {}
    stats = Counter()
    for candidates in event_lines.values():
        chosen = max(candidates, key=lambda group: group["snapshot_timestamp"])
        game, reason = _match_schedule_game(chosen, matchup_index)
        if game is None:
            stats[f"unmatched_events.{reason}"] += 1
            continue
        lines[game["game_pk"]] = float(chosen["point"])
        stats["matched_events"] += 1
    stats["parsed_totals_rows"] = len(parsed)
    stats["mapped_games"] = len(lines)
    return lines, dict(stats)


def _schedule_matchup_index(schedule: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for game in schedule:
        home_name, away_name = _mlb_team_names(game)
        key = (_normalize_team_name(home_name), _normalize_team_name(away_name))
        index.setdefault(key, []).append(game)
    return index


def _match_schedule_game(
    odds_group: Mapping[str, Any],
    matchup_index: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> tuple[Mapping[str, Any] | None, str]:
    key = (
        _normalize_team_name(str(odds_group["home_team"])),
        _normalize_team_name(str(odds_group["away_team"])),
    )
    candidates = list(matchup_index.get(key, ()))
    if not candidates:
        return None, "no_team_match"

    event_time = _utc_instant(odds_group["commence_time"])
    ranked = sorted(
        (
            (abs((_utc_instant(game["game_date"]) - event_time).total_seconds()), game)
            for game in candidates
        ),
        key=lambda item: (item[0], item[1]["game_pk"]),
    )
    nearest_seconds, nearest_game = ranked[0]
    if nearest_seconds > ODDS_MATCH_TOLERANCE_SECONDS:
        return None, "time_out_of_tolerance"
    if len(ranked) > 1 and ranked[1][0] == nearest_seconds:
        return None, "ambiguous_nearest_time"
    return nearest_game, "matched"


def _mlb_team_names(game: Mapping[str, Any]) -> tuple[str, str]:
    raw = game.get("source_game_json")
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, Mapping):
        raise ValueError(f"game_pk={game.get('game_pk')} missing source_game_json mapping")
    try:
        home = payload["teams"]["home"]["team"]["name"]
        away = payload["teams"]["away"]["team"]["name"]
    except KeyError as exc:
        raise ValueError(f"game_pk={game.get('game_pk')} missing team names in source_game_json") from exc
    return str(home), str(away)


def _normalize_team_name(value: str) -> str:
    return " ".join(value.casefold().replace(".", "").split())


def _utc_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def summarize_skips(skipped: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("reason")) for row in skipped))


def append_jsonl_records(
    path: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    key_fields: Sequence[str],
    on_conflict: str = "raise",
) -> int:
    """Append-only JSONL write for detail artifacts, deduped by ``key_fields``.

    Mirrors ``JsonLinesPredictionStore``'s idempotency contract (src/pipelines/
    daily.py) without its prediction-specific required-field schema: identical
    re-writes for an existing key are always a no-op.

    ``on_conflict="raise"`` (default) treats a differing re-write for an
    existing key as an error -- appropriate for ``game_features.jsonl``, where
    the same ``(run_date, game_pk, build_id)`` should deterministically produce
    the same feature values.

    ``on_conflict="overwrite"`` replaces the existing row for that key instead
    (rewriting the whole file) -- appropriate for ``odds_books.jsonl``, which
    tracks the CURRENT price per ``(run_date, game_pk, bookmaker)``, not a full
    tick-by-tick history; without this, ordinary intraday line movement across
    re-runs would either raise or grow the file with a near-duplicate row per
    poll.

    Returns the number of rows written or updated.
    """
    if on_conflict not in ("raise", "overwrite"):
        raise ValueError(f"on_conflict must be 'raise' or 'overwrite', got {on_conflict!r}")

    existing: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            serial = json.loads(line)
            key = tuple(serial[field] for field in key_fields)
            if key not in existing:
                order.append(key)
            existing[key] = serial

    if on_conflict == "raise":
        # Pure append-only path: never rewrites existing lines, matches the
        # original streaming-append cost -- appropriate for an artifact whose
        # rows should never legitimately change once written.
        new_lines: list[str] = []
        written = 0
        for record in records:
            serial = _serialize_detail_record(record)
            key = tuple(serial[field] for field in key_fields)
            prior = existing.get(key)
            if prior is not None:
                if prior == serial:
                    continue
                raise ValueError(f"conflicting re-write for key {key} in {path}")
            new_lines.append(json.dumps(serial, sort_keys=True))
            existing[key] = serial
            written += 1
        if new_lines:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for line in new_lines:
                    handle.write(line + "\n")
        return written

    # on_conflict == "overwrite": an existing row for a key may legitimately
    # need to change (e.g. a bookmaker's price moved since the last run), so
    # the whole file is rewritten with the merged current state.
    changed = 0
    for record in records:
        serial = _serialize_detail_record(record)
        key = tuple(serial[field] for field in key_fields)
        prior = existing.get(key)
        if prior == serial:
            continue
        if key not in existing:
            order.append(key)
        existing[key] = serial
        changed += 1

    if changed == 0:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for key in order:
            handle.write(json.dumps(existing[key], sort_keys=True) + "\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run real local MLB daily predictions.")
    parser.add_argument("--date", default=None, help="Official MLB date YYYY-MM-DD; default local today.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--features-output", default=str(DEFAULT_FEATURES_OUTPUT))
    parser.add_argument("--odds-books-output", default=str(DEFAULT_ODDS_BOOKS_OUTPUT))
    parser.add_argument("--skipped-output", default=str(DEFAULT_SKIPPED_OUTPUT))
    parser.add_argument("--bookmaker", default=DEFAULT_BOOKMAKER)
    parser.add_argument("--regions", default="us")
    parser.add_argument("--odds-json", default=None, help="Optional saved The Odds API payload for offline replay/tests.")
    parser.add_argument("--prediction-timestamp", default=None, help="ISO timestamp with timezone; default now UTC.")
    parser.add_argument("--random-state", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--skip-detail-refresh",
        action="store_true",
        help="Skip MLB game-detail invalidate/backfill before loading starters (offline replay).",
    )
    parser.add_argument("--simulation-output", default=str(DEFAULT_SIMULATION_OUTPUT))
    parser.add_argument(
        "--enable-simulation",
        action="store_true",
        help="Opt in to full-Gold score-model fit and simulation.jsonl writes (off by default).",
    )
    args = parser.parse_args(argv)

    timer = _StepTimer()
    try:
        return _run_operator(args, timer)
    finally:
        timer.log_total()


def _run_operator(args: argparse.Namespace, timer: _StepTimer) -> int:
    run_date = parse_date(args.date)
    certification_path = Path(args.certification)
    api_key = os.environ.get("THE_ODDS_API_KEY")
    if not args.odds_json and not api_key:
        raise RuntimeError("THE_ODDS_API_KEY is not set; refusing to fetch live odds")

    print(f"[gate] ADR-006 locked model: {DEFAULT_MODEL_VERSION}", flush=True)
    estimator, feature_columns, training_build_id, n_train = train_locked_model(
        args.database, certification_path, args.random_state
    )
    print(
        f"[model] trained_rows={n_train} features={len(feature_columns)} "
        f"training_build_id={training_build_id}",
        flush=True,
    )
    timer.log("model_train")

    if args.skip_detail_refresh:
        print("[detail-refresh] skipped", flush=True)
    else:
        detail_stats = refresh_pregame_game_details(
            args.database,
            run_date,
            make_game_detail_fetcher(),
        )
        print(
            f"[detail-refresh] targeted={detail_stats['targeted']} "
            f"invalidated={detail_stats['invalidated']} "
            f"fetched={detail_stats['fetched']} "
            f"normalized={detail_stats['normalized']}",
            flush=True,
        )
    timer.log("detail_refresh")

    print(f"[load] run_date={run_date} database={args.database}", flush=True)
    inputs = load_prediction_inputs(args.database, run_date, certification_path)
    print(
        f"[slate] games={len(inputs['schedule'])} team_rows={len(inputs['team_stats'])} "
        f"appearances={len(inputs['appearances'])} starter_rows={len(inputs['starters'])}",
        flush=True,
    )
    if not inputs["schedule"]:
        print("[done] no active regular-season games found for date", flush=True)
        return 0

    ready_schedule, starter_skipped = partition_schedule_by_announced_starters(
        inputs["schedule"],
        inputs["starters"],
        run_date=run_date,
    )
    skipped_written = replace_skipped_for_run_date(
        Path(args.skipped_output),
        run_date,
        starter_skipped,
    )
    if starter_skipped:
        print(
            f"[starters] awaiting_announcement={len(starter_skipped)} "
            f"written={skipped_written} output={args.skipped_output}",
            flush=True,
        )
    timer.log("load_slate")

    if not ready_schedule:
        print("[done] no games with both starting pitchers announced yet", flush=True)
        return 0

    prediction_inputs = {**inputs, "schedule": ready_schedule}
    components = build_today_feature_components(prediction_inputs)
    today_matrix = build_feature_matrix(
        ready_schedule,
        team_features=components["team"],
        starter_features=components["starter"],
        bullpen_features=components["bullpen"],
        results=[],
        certification=prediction_inputs["certification"],
        completeness_mode="inference",
    )
    print(
        f"[gold] rows={len(today_matrix['rows'])} excluded={len(today_matrix['excluded'])} "
        f"columns={len(today_matrix['feature_columns'])} completeness={today_matrix['feature_completeness']['status']}",
        flush=True,
    )
    if today_matrix["excluded"]:
        excluded = [
            {
                "game_pk": row["game_pk"],
                "missing_components": row["missing_components"],
                "reason": row["reason"],
            }
            for row in today_matrix["excluded"]
        ]
        print(f"[gold-excluded] {json.dumps(excluded, sort_keys=True)}", flush=True)
    timer.log("gold_build")

    score_model = None
    if not args.enable_simulation:
        print("[simulation] skipped (Monte Carlo disabled by default)", flush=True)
    else:
        score_model, sim_training_build_id, n_sim_train = train_locked_score_model(
            args.database,
            certification_path,
            args.random_state,
        )
        print(
            f"[simulation] trained_rows={n_sim_train} "
            f"training_build_id={sim_training_build_id}",
            flush=True,
        )

    # The canonical fetch is scoped to exactly the configured bookmaker, same
    # as before this task -- unaffected by anything below. The comparison
    # fetch/parse is best-effort: a broader multi-book payload has a larger
    # surface for one minor book's bad data to fail the strict parser, and a
    # display-only comparison feature must never take down the canonical
    # prediction run over that.
    comparison_payload = None
    if args.odds_json:
        payload = json.loads(Path(args.odds_json).read_text(encoding="utf-8"))
        print(f"[odds] loaded payload={args.odds_json}", flush=True)
        comparison_payload = payload
    else:
        print(f"[odds] fetching bookmaker={args.bookmaker} regions={args.regions}", flush=True)
        payload = fetch_odds_payload(api_key, regions=args.regions, bookmakers=args.bookmaker, timeout=args.timeout)
        print(f"[odds-books] fetching regions={args.regions} (all books, comparison only)", flush=True)
        try:
            comparison_payload = fetch_odds_payload(api_key, regions=args.regions, bookmakers=None, timeout=args.timeout)
        except RuntimeError as exc:
            print(f"[odds-books] comparison fetch failed, skipping: {exc}", flush=True)

    odds_snapshots, odds_stats = odds_snapshots_for_schedule(
        payload, ready_schedule, bookmaker=args.bookmaker
    )
    print(f"[odds] {json.dumps(odds_stats, sort_keys=True)}", flush=True)

    all_books: dict[tuple[Any, str], dict[str, Any]] = {}
    if comparison_payload is not None:
        try:
            all_books, all_books_stats = all_book_snapshots_for_schedule(
                comparison_payload, ready_schedule
            )
            print(f"[odds-books] {json.dumps(all_books_stats, sort_keys=True)}", flush=True)
        except OddsDataError as exc:
            print(f"[odds-books] comparison payload malformed, skipping: {exc}", flush=True)
    timer.log("odds_fetch")

    if score_model is not None:
        totals_lines: dict[Any, float] = {}
        if comparison_payload is not None:
            try:
                totals_lines, totals_stats = totals_lines_for_schedule(
                    comparison_payload,
                    ready_schedule,
                    bookmaker=args.bookmaker,
                )
                print(
                    f"[simulation] totals {json.dumps(totals_stats, sort_keys=True)}",
                    flush=True,
                )
            except OddsDataError as exc:
                print(
                    f"[simulation] totals parse failed, skipping lines: {exc}",
                    flush=True,
                )
        simulation_records = build_simulation_records(
            today_matrix["rows"],
            score_model=score_model,
            run_date=run_date,
            build_id=today_matrix["build_id"],
            n_trials=DEFAULT_SIMULATION_TRIALS,
            random_state=args.random_state,
            totals_lines=totals_lines,
        )
        simulation_written = append_jsonl_records(
            Path(args.simulation_output),
            simulation_records,
            key_fields=("run_date", "game_pk"),
            on_conflict="overwrite",
        )
        print(
            f"[simulation] games={len(simulation_records)} written={simulation_written} "
            f"output={args.simulation_output}",
            flush=True,
        )
        timer.log("simulation")

    # ADR-002: odds_snapshot < prediction_timestamp < first_pitch. The cutoff must
    # be taken after odds are fetched, not at operator start -- a long model train
    # or detail refresh otherwise makes fresh odds look "from the future".
    prediction_timestamp = parse_timestamp(args.prediction_timestamp)

    store = JsonLinesPredictionStore(args.output)
    result = run_daily_predictions(
        run_date=run_date,
        schedule=ready_schedule,
        team_features=components["team"],
        starter_features=components["starter"],
        bullpen_features=components["bullpen"],
        certification=prediction_inputs["certification"],
        estimator=estimator,
        model_version=DEFAULT_MODEL_VERSION,
        training_feature_columns=feature_columns,
        odds_snapshots=odds_snapshots,
        prediction_timestamp=prediction_timestamp,
        store=store,
        results=[],
        feature_schema_version=training_build_id,
    )
    print(
        f"[predictions] records={len(result.records)} written={len(result.written)} "
        f"skipped={len(result.skipped)} output={args.output}",
        flush=True,
    )
    print(f"[skips] {json.dumps(summarize_skips(result.skipped), sort_keys=True)}", flush=True)
    if result.skipped:
        print(
            f"[skips-detail] {json.dumps([dict(row) for row in result.skipped], sort_keys=True)}",
            flush=True,
        )
    timer.log("predictions")

    # Tracks the CURRENT feature snapshot per (run_date, game_pk, build_id).
    # A same-day re-run with unchanged Silver inputs is idempotent; after a
    # detail refresh or newly announced starter the features legitimately change
    # and should overwrite the prior row (same contract as odds_books.jsonl).
    features_records = [
        {
            "run_date": run_date,
            "game_pk": row["game_pk"],
            "build_id": today_matrix["build_id"],
            "features": row["features"],
        }
        for row in today_matrix["rows"]
    ]
    features_written = append_jsonl_records(
        Path(args.features_output),
        features_records,
        key_fields=("run_date", "game_pk", "build_id"),
        on_conflict="overwrite",
    )
    print(
        f"[features] written={features_written} output={args.features_output}",
        flush=True,
    )

    # "overwrite": tracks the CURRENT price per (run_date, game_pk, bookmaker),
    # not a tick-by-tick history -- ordinary intraday line movement across
    # re-runs updates the row instead of raising or growing the file forever.
    odds_book_records = [
        {
            "run_date": run_date,
            "game_pk": game_pk,
            "bookmaker": bookmaker,
            "home_american": snapshot["home_american"],
            "away_american": snapshot["away_american"],
            "snapshot_timestamp": snapshot["snapshot_timestamp"],
            "source": snapshot["source"],
        }
        for (game_pk, bookmaker), snapshot in all_books.items()
    ]
    odds_books_written = append_jsonl_records(
        Path(args.odds_books_output),
        odds_book_records,
        key_fields=("run_date", "game_pk", "bookmaker"),
        on_conflict="overwrite",
    )
    print(
        f"[odds-books] written={odds_books_written} output={args.odds_books_output}",
        flush=True,
    )
    timer.log("artifact_writes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
