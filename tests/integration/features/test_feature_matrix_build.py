"""End-to-end integration: build the Gold matrix from the real component builders.

Feeds tiny ``silver``-shaped fixtures through ``build_team_features`` /
``build_starter_features`` / ``build_bullpen_features`` and pivots the results
with ``build_feature_matrix`` (FEAT-004).
"""

from __future__ import annotations

from datetime import datetime, timezone

from features.build import build_feature_matrix
from features.bullpen import build_bullpen_features
from features.starter import build_starter_features
from features.team import build_team_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _cert() -> dict:
    return {"status": "PASS", "dataset": {"fingerprint": "7225f7f46a5e27e9"}}


def _game(game_pk: int, home_id: int, away_id: int, date: str) -> dict:
    return {
        "game_pk": game_pk,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "game_date": _dt(date),
        "game_type": "R",
        "season": "2024",
    }


def _tgs(game_pk: int, team_id: int, side: str, date: str, score: int, won: bool) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(date),
        "season": "2024",
        "score": score,
        "is_winner": won,
    }


def _starter_app(
    game_pk: int, team_id: int, side: str, pitcher_id: int, date: str, *, er: int
) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(date),
        "game_type": "R",
        "pitcher_id": pitcher_id,
        "appearance_order": 1,
        "is_actual_starter": True,
        "outs_recorded": 18,
        "earned_runs": er,
        "hits_allowed": 5,
        "walks": 1,
        "strikeouts": 6,
        "batters_faced": 24,
        "pitches_thrown": 90,
    }


def _reliever_app(
    game_pk: int, team_id: int, side: str, pitcher_id: int, order: int, date: str
) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(date),
        "game_type": "R",
        "pitcher_id": pitcher_id,
        "appearance_order": order,
        "is_actual_starter": False,
        "outs_recorded": 3,
        "earned_runs": 1,
        "hits_allowed": 2,
        "walks": 1,
        "strikeouts": 1,
        "batters_faced": 5,
        "pitches_thrown": 20,
    }


def _team_game_appearances(
    game_pk: int, team_id: int, side: str, starter_id: int, r1: int, r2: int, date: str, *, er: int
) -> list[dict]:
    return [
        _starter_app(game_pk, team_id, side, starter_id, date, er=er),
        _reliever_app(game_pk, team_id, side, r1, 2, date),
        _reliever_app(game_pk, team_id, side, r2, 3, date),
    ]


def _fixtures():
    """Two regular-season games between teams 10 and 20 (home flips)."""
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00"),
        _game(2, 20, 10, "2024-04-05T19:00:00"),
    ]
    tgs = [
        _tgs(1, 10, "home", "2024-04-01T19:00:00", 5, True),
        _tgs(1, 20, "away", "2024-04-01T19:00:00", 3, False),
        _tgs(2, 20, "home", "2024-04-05T19:00:00", 2, False),
        _tgs(2, 10, "away", "2024-04-05T19:00:00", 6, True),
    ]
    appearances = (
        _team_game_appearances(1, 10, "home", 100, 101, 102, "2024-04-01T19:00:00", er=2)
        + _team_game_appearances(1, 20, "away", 200, 201, 202, "2024-04-01T19:00:00", er=4)
        + _team_game_appearances(2, 20, "home", 200, 201, 202, "2024-04-05T19:00:00", er=1)
        + _team_game_appearances(2, 10, "away", 100, 101, 102, "2024-04-05T19:00:00", er=3)
    )
    return games, tgs, appearances


def _build_matrix(games, tgs, appearances):
    team = build_team_features(tgs)
    starter = build_starter_features(appearances, games)
    bullpen = build_bullpen_features(appearances)
    return build_feature_matrix(
        games,
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        results=tgs,
        certification=_cert(),
        completeness_mode="inference",
    )


def test_end_to_end_matrix_shape_and_uniqueness() -> None:
    matrix = _build_matrix(*_fixtures())
    assert [row["game_pk"] for row in matrix["rows"]] == [1, 2]
    assert len({row["game_pk"] for row in matrix["rows"]}) == 2
    assert matrix["build_id"] == "7225f7f46a5e27e9"


def test_end_to_end_home_away_and_target() -> None:
    matrix = _build_matrix(*_fixtures())
    by_pk = {row["game_pk"]: row for row in matrix["rows"]}

    # Game 1: both teams cold-start; home team 10 wins.
    assert by_pk[1]["home_team_id"] == 10
    assert by_pk[1]["target"] == {"home_win": True}

    # Game 2: home team 20 (lost game 1) vs away team 10 (won game 1).
    g2 = by_pk[2]["features"]
    assert by_pk[2]["home_team_id"] == 20
    assert g2["home_team_win_pct_before"] == 0.0   # team 20 lost its only prior
    assert g2["away_team_win_pct_before"] == 1.0   # team 10 won its only prior
    assert g2["diff_team_win_pct_before"] == -1.0
    # starter history exists for game 2 (each pitcher started game 1)
    assert g2["home_starter_season_era_before"] is not None
    assert g2["away_starter_season_era_before"] is not None
    assert by_pk[2]["target"] == {"home_win": False}


def test_end_to_end_target_absent_from_features() -> None:
    matrix = _build_matrix(*_fixtures())
    assert all("home_win" not in c for c in matrix["feature_columns"])
    for row in matrix["rows"]:
        assert "home_win" not in row["features"]
        assert sorted(row["features"]) == matrix["feature_columns"]
