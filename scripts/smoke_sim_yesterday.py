"""Smoke: run score-model simulation on the last 8 games from a daily slate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402

from features.build import build_feature_matrix  # noqa: E402
from features.bullpen import build_bullpen_features  # noqa: E402
from features.starter import build_starter_features  # noqa: E402
from features.team import build_team_features  # noqa: E402
from simulation.game_level import SimulationConfig, simulate_game  # noqa: E402
from simulation.score_model import fit_score_model  # noqa: E402

TEAM_ABBREV = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}
DEFAULT_CERT = ROOT / "state/data-certifications/certification-PASS-a910017bac839af5.json"
DEFAULT_FEATURES = ROOT / "state/predictions/game_features.jsonl"
DEFAULT_PREDICTIONS = ROOT / "state/predictions/daily.jsonl"


def _rows(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cursor = connection.execute(sql)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_features(path: Path, run_date: str) -> dict[int, dict]:
    by_pk: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if str(record.get("run_date")) != run_date:
            continue
        by_pk[int(record["game_pk"])] = record["features"]
    return by_pk


def _load_xgb(path: Path, run_date: str) -> dict[int, list[dict]]:
    by_pk: dict[int, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if str(record.get("run_date")) != run_date:
            continue
        by_pk.setdefault(int(record["game_pk"]), []).append(record)
    return by_pk


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-08-13", help="Daily slate run_date")
    parser.add_argument("--count", type=int, default=8, help="Number of games (last N by game_pk)")
    parser.add_argument("--trials", type=int, default=10_000, help="Monte Carlo trials per game")
    args = parser.parse_args()

    features_by_pk = _load_features(DEFAULT_FEATURES, args.date)
    if not features_by_pk:
        raise SystemExit(f"no game_features rows for run_date={args.date}")

    game_pks = sorted(features_by_pk)[-args.count :]
    xgb_by_pk = _load_xgb(DEFAULT_PREDICTIONS, args.date)
    cert = json.loads(DEFAULT_CERT.read_text(encoding="utf-8"))

    print(f"Smoke slate run_date={args.date} games={len(game_pks)}")
    print(f"game_pks={game_pks}")

    with duckdb.connect(str(ROOT / "data/mlb.duckdb"), read_only=True) as connection:
        games = _rows(
            connection,
            """
            SELECT game_pk, season, game_type, game_date, home_team_id,
                   away_team_id, game_number
            FROM silver.games
            WHERE season IN ('2021', '2022', '2023', '2024', '2025')
              AND game_type = 'R'
            ORDER BY game_pk
            """,
        )
        team_stats = _rows(
            connection,
            """
            SELECT t.game_pk, t.team_id, t.side, t.score, t.is_winner,
                   t.game_date, g.season
            FROM silver.team_game_statistics t
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
              AND g.game_type = 'R'
            ORDER BY t.game_pk, t.team_id
            """,
        )
        appearances = _rows(
            connection,
            """
            SELECT p.game_pk, p.team_id, p.side, g.game_date, g.game_type,
                   g.game_number, p.pitcher_id, p.appearance_order,
                   p.is_actual_starter, p.outs_recorded, p.batters_faced,
                   p.pitches_thrown, p.earned_runs, p.hits_allowed, p.walks,
                   p.strikeouts, p.home_runs_allowed
            FROM silver.pitcher_appearances p
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
            ORDER BY p.game_pk, p.team_id, p.appearance_order
            """,
        )
        starters = _rows(
            connection,
            """
            SELECT ps.game_pk, ps.team_id, ps.side, ps.actual_pitcher_id,
                   ps.probable_pitcher_id
            FROM silver.pitcher_starters ps
            JOIN silver.games g USING (game_pk)
            WHERE g.season IN ('2021', '2022', '2023', '2024', '2025')
            ORDER BY ps.game_pk, ps.team_id
            """,
        )
        placeholders = ", ".join(str(pk) for pk in game_pks)
        meta_rows = connection.execute(
            f"""
            SELECT g.game_pk, g.home_team_id, g.away_team_id, h.score, a.score
            FROM silver.games g
            JOIN silver.team_game_statistics h
              ON h.game_pk = g.game_pk AND h.side = 'home'
            JOIN silver.team_game_statistics a
              ON a.game_pk = g.game_pk AND a.side = 'away'
            WHERE g.game_pk IN ({placeholders})
            """
        ).fetchall()

    meta = {
        int(game_pk): (int(home_id), int(away_id), int(home_score), int(away_score))
        for game_pk, home_id, away_id, home_score, away_score in meta_rows
        if home_score is not None and away_score is not None
    }

    team = build_team_features(team_stats)
    starter = build_starter_features(appearances, games, starters)
    bullpen = build_bullpen_features(appearances)
    matrix = build_feature_matrix(
        games,
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        results=team_stats,
        certification=cert,
        completeness_mode="historical",
    )

    scores: dict[int, dict[str, int]] = {}
    for row in team_stats:
        if row["score"] is None:
            continue
        scores.setdefault(int(row["game_pk"]), {})[row["side"]] = int(row["score"])

    training: list[dict] = []
    for row in matrix["rows"]:
        game_pk = int(row["game_pk"])
        side_scores = scores.get(game_pk)
        if not side_scores or "home" not in side_scores or "away" not in side_scores:
            continue
        training.append(
            {
                "game_pk": game_pk,
                "features": row["features"],
                "home_runs": side_scores["home"],
                "away_runs": side_scores["away"],
            }
        )

    model = fit_score_model(training, random_state=0)
    config = SimulationConfig(n_trials=args.trials, random_state=42, store_trials=True)
    print(f"training_rows={len(training)} feature_cols={len(model.feature_names)} trials={args.trials}")
    print()
    header = (
        f"{'game_pk':<10} {'matchup':<12} {'actual':<6} {'P(home)':>8} "
        f"{'sim':<6} {'xgb':<6} {'E[tot]':>7} {'act':>5}"
    )
    print(header)
    print("-" * len(header))

    simulated = 0
    for game_pk in game_pks:
        features = features_by_pk[game_pk]
        if game_pk not in meta:
            print(f"{game_pk:<10} {'?':<12} {'?':<6} {'n/a':>8} {'n/a':<6} {'n/a':<6} {'n/a':>7} {'n/a':>5}")
            continue
        home_id, away_id, home_score, away_score = meta[game_pk]
        matchup = f"{TEAM_ABBREV[away_id]}@{TEAM_ABBREV[home_id]}"
        actual_winner = TEAM_ABBREV[home_id] if home_score > away_score else TEAM_ABBREV[away_id]
        result = simulate_game(features, score_model=model, config=config, game_pk=game_pk)
        sim_pick = TEAM_ABBREV[home_id] if result.p_home_win >= 0.5 else TEAM_ABBREV[away_id]
        xgb_pick = "n/a"
        preds = xgb_by_pk.get(game_pk, [])
        if preds:
            latest = sorted(preds, key=lambda row: str(row["prediction_timestamp"]))[-1]
            xgb_pick = (
                TEAM_ABBREV[home_id]
                if latest["model_probability"] >= 0.5
                else TEAM_ABBREV[away_id]
            )
        simulated += 1
        print(
            f"{game_pk:<10} {matchup:<12} {actual_winner:<6} {result.p_home_win:8.3f} "
            f"{sim_pick:<6} {xgb_pick:<6} {result.total_runs_mean:7.2f} {home_score + away_score:5d}"
        )

    print()
    print(f"SMOKE PASS: simulated {simulated}/{len(game_pks)} games without error")


if __name__ == "__main__":
    main()
