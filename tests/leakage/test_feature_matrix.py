"""Leakage / target-isolation tests for the Gold feature matrix (ADR-002).

The aggregator must never route post-game outcome fields (score / is_winner /
the derived ``home_win`` label) into the predictive feature namespace, and it
must preserve the component builders' point-in-time safety end to end.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from features.build import build_feature_matrix
from features.bullpen import build_bullpen_features
from features.starter import build_starter_features
from features.team import build_team_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _cert() -> dict:
    return {"status": "PASS", "dataset": {"fingerprint": "fp0001"}}


# --- Aggregator-level isolation: fixed features, mutate only results ---------

def _component_rows(game_pk: int, home_id: int, away_id: int) -> dict:
    def team(team_id, win_pct):
        return {
            "game_pk": game_pk,
            "team_id": team_id,
            "side": "home",
            "is_home": True,
            "game_date": _dt("2024-04-01T19:00:00"),
            "win_pct_before": win_pct,
        }

    def starter(team_id, pid):
        return {
            "game_pk": game_pk,
            "team_id": team_id,
            "starter_pitcher_id": pid,
            "season_era_before": 3.0,
        }

    def bullpen(team_id, ip):
        return {"game_pk": game_pk, "team_id": team_id, "bullpen_ip_L7": ip}

    return {
        "team": [team(home_id, 0.6), team(away_id, 0.4)],
        "starter": [starter(home_id, 100), starter(away_id, 200)],
        "bullpen": [bullpen(home_id, 7.0), bullpen(away_id, 6.0)],
    }


def _matrix_with_results(results: list[dict]):
    comp = _component_rows(1, 10, 20)
    games = [
        {
            "game_pk": 1,
            "home_team_id": 10,
            "away_team_id": 20,
            "game_date": _dt("2024-04-01T19:00:00"),
            "game_type": "R",
        }
    ]
    return build_feature_matrix(
        games,
        team_features=comp["team"],
        starter_features=comp["starter"],
        bullpen_features=comp["bullpen"],
        results=results,
        certification=_cert(),
    )


def _result(team_id, score, won):
    return {"game_pk": 1, "team_id": team_id, "score": score, "is_winner": won}


def test_result_mutation_moves_only_the_target() -> None:
    base = _matrix_with_results([_result(10, 5, True), _result(20, 3, False)])
    flipped = _matrix_with_results([_result(10, 1, False), _result(20, 9, True)])

    # Features are byte-for-byte identical regardless of the post-game outcome.
    assert base["rows"][0]["features"] == flipped["rows"][0]["features"]
    assert base["feature_columns"] == flipped["feature_columns"]
    # Only the isolated target reflects the outcome.
    assert base["rows"][0]["target"] == {"home_win": True}
    assert flipped["rows"][0]["target"] == {"home_win": False}


def test_no_feature_column_names_a_post_game_or_target_field() -> None:
    matrix = _matrix_with_results([_result(10, 5, True), _result(20, 3, False)])
    # The label token must never appear anywhere in the feature namespace ...
    for column in matrix["feature_columns"]:
        assert "home_win" not in column, column
    # ... and no feature key may *be* a raw post-game field.
    post_game = {"home_win", "is_winner", "score"}
    for row in matrix["rows"]:
        assert post_game.isdisjoint(row["features"])


# --- End-to-end: future outcomes must not rewrite earlier feature rows -------

def _game(game_pk, home_id, away_id, date):
    return {
        "game_pk": game_pk,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "game_date": _dt(date),
        "game_type": "R",
        "season": "2024",
    }


def _tgs(game_pk, team_id, side, date, score, won):
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(date),
        "season": "2024",
        "score": score,
        "is_winner": won,
    }


def _apps(game_pk, team_id, side, starter_id, date, er):
    def app(pid, order, is_starter):
        return {
            "game_pk": game_pk,
            "team_id": team_id,
            "side": side,
            "game_date": _dt(date),
            "game_type": "R",
            "pitcher_id": pid,
            "appearance_order": order,
            "is_actual_starter": is_starter,
            "outs_recorded": 18 if is_starter else 3,
            "earned_runs": er if is_starter else 1,
            "hits_allowed": 5 if is_starter else 2,
            "walks": 1,
            "strikeouts": 6 if is_starter else 1,
            "batters_faced": 24 if is_starter else 5,
            "pitches_thrown": 90 if is_starter else 20,
        }

    return [
        app(starter_id, 1, True),
        app(starter_id + 1, 2, False),
        app(starter_id + 2, 3, False),
    ]


def _three_game_fixtures():
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00"),
        _game(2, 20, 10, "2024-04-05T19:00:00"),
        _game(3, 10, 20, "2024-04-09T19:00:00"),
    ]
    tgs = [
        _tgs(1, 10, "home", "2024-04-01T19:00:00", 5, True),
        _tgs(1, 20, "away", "2024-04-01T19:00:00", 3, False),
        _tgs(2, 20, "home", "2024-04-05T19:00:00", 2, False),
        _tgs(2, 10, "away", "2024-04-05T19:00:00", 6, True),
        _tgs(3, 10, "home", "2024-04-09T19:00:00", 4, True),
        _tgs(3, 20, "away", "2024-04-09T19:00:00", 1, False),
    ]
    appearances = (
        _apps(1, 10, "home", 100, "2024-04-01T19:00:00", 2)
        + _apps(1, 20, "away", 200, "2024-04-01T19:00:00", 4)
        + _apps(2, 20, "home", 200, "2024-04-05T19:00:00", 1)
        + _apps(2, 10, "away", 100, "2024-04-05T19:00:00", 3)
        + _apps(3, 10, "home", 100, "2024-04-09T19:00:00", 2)
        + _apps(3, 20, "away", 200, "2024-04-09T19:00:00", 5)
    )
    return games, tgs, appearances


def _build(games, tgs, appearances):
    return build_feature_matrix(
        games,
        team_features=build_team_features(tgs),
        starter_features=build_starter_features(appearances, games),
        bullpen_features=build_bullpen_features(appearances),
        results=tgs,
        certification=_cert(),
    )


def test_future_game_outcome_does_not_change_earlier_features() -> None:
    games, tgs, appearances = _three_game_fixtures()
    baseline = _build(games, tgs, appearances)
    base_features = {r["game_pk"]: r["features"] for r in baseline["rows"]}

    # Rewrite game 3's outcome (a future result relative to games 1 and 2).
    mutated_tgs = deepcopy(tgs)
    for row in mutated_tgs:
        if row["game_pk"] == 3:
            row["score"] = 99 if row["team_id"] == 20 else 0
            row["is_winner"] = row["team_id"] == 20
    after = _build(games, mutated_tgs, appearances)
    after_features = {r["game_pk"]: r["features"] for r in after["rows"]}

    for game_pk in (1, 2):
        assert after_features[game_pk] == base_features[game_pk], game_pk
    # Sanity: the harness is sensitive — game 3's target flipped.
    after_targets = {r["game_pk"]: r["target"] for r in after["rows"]}
    assert after_targets[3] == {"home_win": False}


def test_current_game_result_absent_from_its_own_features() -> None:
    games, tgs, appearances = _three_game_fixtures()
    baseline = _build(games, tgs, appearances)
    base_features = {r["game_pk"]: r["features"] for r in baseline["rows"]}

    # Blow out game 2's own outcome; its own feature row must not move.
    mutated_tgs = deepcopy(tgs)
    for row in mutated_tgs:
        if row["game_pk"] == 2:
            row["score"] = 50 if row["team_id"] == 20 else 0
            row["is_winner"] = row["team_id"] == 20
    after = _build(games, mutated_tgs, appearances)
    after_features = {r["game_pk"]: r["features"] for r in after["rows"]}

    assert after_features[2] == base_features[2]
