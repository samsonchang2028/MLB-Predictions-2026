"""Unit tests for the APP-005 game detail data-loading/shaping module.

Exercises the prediction lookup, missing-artifact tolerance (older
predictions with no PIPE-004 feature/odds rows), feature grouping by
component, and the multi-book odds comparison. No Streamlit import here.
"""

from __future__ import annotations

import json

from app.game_detail import load_game_detail


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _record(game_pk=1, *, run_date="2026-08-12", home_id=147, away_id=111, edge=0.05):
    return {
        "game_pk": game_pk,
        "model_probability": 0.55,
        "market_probability": 0.55 - edge,
        "edge": edge,
        "odds_snapshot_timestamp": "2026-08-12T14:00:00+00:00",
        "prediction_timestamp": "2026-08-12T15:00:00+00:00",
        "game_start_timestamp": "2026-08-12T23:10:00+00:00",
        "model_version": "v1",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "run_date": run_date,
        "source": "the_odds_api:draftkings",
        "home_american": -150,
        "away_american": 130,
    }


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_returns_none_when_no_matching_prediction(tmp_path):
    result = load_game_detail(
        999,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )
    assert result is None


def test_missing_artifacts_are_tolerated_not_an_error(tmp_path):
    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )
    assert result is not None
    assert result["matchup"] == "BOS @ NYY"
    assert result["model_probability"] == 0.55
    assert result["features"] is None
    assert result["odds_books"] == []


def test_features_grouped_by_component(tmp_path):
    features_path = tmp_path / "game_features.jsonl"
    _write_jsonl(
        features_path,
        [
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "build_id": "b1",
                "prediction_timestamp": "2026-08-12T15:00:00+00:00",
                "features": {
                    "home_starter_season_era_before": 3.5,
                    "away_starter_season_era_before": 4.1,
                    "diff_starter_season_era_before": -0.6,
                    "home_bullpen_era_L7": 3.0,
                    "diff_team_win_pct_before": 0.05,
                },
            }
        ],
    )

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1)]),
        features_path=features_path,
        odds_books_path=tmp_path / "odds_books.jsonl",
    )

    assert set(result["features"]["starter"]) == {
        "home_starter_season_era_before",
        "away_starter_season_era_before",
        "diff_starter_season_era_before",
    }
    assert result["features"]["bullpen"] == {"home_bullpen_era_L7": 3.0}
    assert result["features"]["team"] == {"diff_team_win_pct_before": 0.05}


def test_odds_books_computes_implied_probability_and_keeps_latest_per_book(tmp_path):
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(
        odds_path,
        [
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "bookmaker": "draftkings",
                "home_american": -120,
                "away_american": 100,
                "snapshot_timestamp": "2026-08-12T10:00:00+00:00",
                "source": "the_odds_api",
            },
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "bookmaker": "draftkings",
                "home_american": -150,
                "away_american": 130,
                "snapshot_timestamp": "2026-08-12T14:00:00+00:00",
                "source": "the_odds_api",
            },
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "bookmaker": "fanduel",
                "home_american": -140,
                "away_american": 120,
                "snapshot_timestamp": "2026-08-12T14:00:00+00:00",
                "source": "the_odds_api",
            },
        ],
    )

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=odds_path,
    )

    books = {book["bookmaker"]: book for book in result["odds_books"]}
    assert set(books) == {"draftkings", "fanduel"}
    # latest draftkings snapshot (-150/130) wins over the earlier one (-120/100).
    assert books["draftkings"]["home_american"] == -150
    assert books["draftkings"]["implied_home_probability"] > 0
    assert books["draftkings"]["model_vs_book_delta"] == (
        0.55 - books["draftkings"]["implied_home_probability"]
    )
