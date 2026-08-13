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
from ingestion.odds import parse_the_odds_api_moneylines  # noqa: E402
from market import MarketLabel  # noqa: E402
from models import xgboost_model  # noqa: E402
from pipelines.daily import JsonLinesPredictionStore, run_daily_predictions  # noqa: E402
from rerun_repaired_experiment import _build_matrix as _build_training_matrix  # noqa: E402
from rerun_repaired_experiment import _load_inputs as _load_training_inputs  # noqa: E402

DEFAULT_CERTIFICATION = (
    Path("state")
    / "data-certifications"
    / "certification-PASS-a910017bac839af5.json"
)
DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_OUTPUT = Path("state") / "predictions" / "daily.jsonl"
DEFAULT_MODEL_VERSION = "v1-adr-006-tuned-xgboost-expanding-uncalibrated"
DEFAULT_BOOKMAKER = "draftkings"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
ODDS_MATCH_TOLERANCE_SECONDS = 12 * 60 * 60


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run real local MLB daily predictions.")
    parser.add_argument("--date", default=None, help="Official MLB date YYYY-MM-DD; default local today.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--bookmaker", default=DEFAULT_BOOKMAKER)
    parser.add_argument("--regions", default="us")
    parser.add_argument("--odds-json", default=None, help="Optional saved The Odds API payload for offline replay/tests.")
    parser.add_argument("--prediction-timestamp", default=None, help="ISO timestamp with timezone; default now UTC.")
    parser.add_argument("--random-state", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    run_date = parse_date(args.date)
    prediction_timestamp = parse_timestamp(args.prediction_timestamp)
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

    starter_placeholders = _starter_placeholder_rows(inputs["schedule"], inputs["starters"])
    if starter_placeholders:
        print(f"[starters] unknown_placeholders={len(starter_placeholders)}", flush=True)
    components = build_today_feature_components(inputs)
    today_matrix = build_feature_matrix(
        inputs["schedule"],
        team_features=components["team"],
        starter_features=components["starter"],
        bullpen_features=components["bullpen"],
        results=[],
        certification=inputs["certification"],
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

    if args.odds_json:
        payload = json.loads(Path(args.odds_json).read_text(encoding="utf-8"))
        print(f"[odds] loaded payload={args.odds_json}", flush=True)
    else:
        print(f"[odds] fetching bookmaker={args.bookmaker} regions={args.regions}", flush=True)
        payload = fetch_odds_payload(api_key, regions=args.regions, bookmakers=args.bookmaker, timeout=args.timeout)

    odds_snapshots, odds_stats = odds_snapshots_for_schedule(payload, inputs["schedule"], bookmaker=args.bookmaker)
    print(f"[odds] {json.dumps(odds_stats, sort_keys=True)}", flush=True)

    store = JsonLinesPredictionStore(args.output)
    result = run_daily_predictions(
        run_date=run_date,
        schedule=inputs["schedule"],
        team_features=components["team"],
        starter_features=components["starter"],
        bullpen_features=components["bullpen"],
        certification=inputs["certification"],
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
