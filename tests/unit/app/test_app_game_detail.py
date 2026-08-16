"""Unit tests for the APP-005 game detail data-loading/shaping module.

Exercises the prediction lookup, missing-artifact tolerance (older
predictions with no PIPE-004 feature/odds rows), feature grouping by
component, and the multi-book odds comparison. No Streamlit import here.
"""

from __future__ import annotations

import json

from app.board import DEFAULT_EDGE_THRESHOLD
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


def test_returns_none_for_malformed_prediction_record_instead_of_raising(tmp_path):
    malformed = _record(1)
    del malformed["edge"]  # matches app.board._record_problem's own required-field check

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([malformed]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )
    assert result is None


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


def test_verdict_favors_home_and_is_a_play_when_edge_meets_threshold(tmp_path):
    edge = DEFAULT_EDGE_THRESHOLD + 0.01
    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=edge)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )

    assert result["play"] is True
    assert result["model_side_team"] == "NYY"  # home_id=147
    assert result["action_label"] == "PLAY NYY"
    assert result["model_probability_favored"] == result["model_probability"]
    assert result["market_probability_favored"] == result["market_probability"]


def test_verdict_favors_away_and_flips_probabilities_when_edge_negative(tmp_path):
    edge = -(DEFAULT_EDGE_THRESHOLD + 0.01)
    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=edge)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )

    assert result["play"] is True
    assert result["model_side_team"] == "BOS"  # away_id=111
    assert result["action_label"] == "PLAY BOS"
    assert result["model_probability_favored"] == 1 - result["model_probability"]
    assert result["market_probability_favored"] == 1 - result["market_probability"]


def test_verdict_is_pass_when_edge_below_display_threshold(tmp_path):
    edge = DEFAULT_EDGE_THRESHOLD - 0.005
    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=edge)]),
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=tmp_path / "odds_books.jsonl",
    )

    assert result["play"] is False
    assert result["action_label"] == "PASS"


def test_notable_stat_gaps_ranked_by_magnitude_and_capped_at_three(tmp_path):
    features_path = tmp_path / "game_features.jsonl"
    _write_jsonl(
        features_path,
        [
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "build_id": "b1",
                "features": {
                    "home_starter_season_era_before": 3.5,
                    "away_starter_season_era_before": 4.1,
                    "diff_starter_season_era_before": -0.6,
                    "home_bullpen_era_L7": 3.0,
                    "away_bullpen_era_L7": 3.05,
                    "diff_bullpen_era_L7": -0.05,
                    "home_team_win_pct_before": 0.6,
                    "away_team_win_pct_before": 0.4,
                    "diff_team_win_pct_before": 0.2,
                    "diff_starter_days_rest": 1,  # no matching home_/away_ pair -> excluded
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

    gaps = result["notable_stat_gaps"]
    assert len(gaps) == 3
    # ranked by |diff| descending: team win pct (0.2) > starter era (-0.6 is
    # actually largest magnitude) -- assert the ranking, not a guessed order.
    assert [round(abs(g["diff"]), 3) for g in gaps] == sorted(
        (round(abs(g["diff"]), 3) for g in gaps), reverse=True
    )
    assert {g["label"] for g in gaps} == {
        "Starter season era before",
        "Bullpen era l7",
        "Team win pct before",
    }
    top = gaps[0]
    assert top["home_team"] == "NYY"
    assert top["away_team"] == "BOS"


def test_best_price_picks_highest_payout_for_favored_side(tmp_path):
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(
        odds_path,
        [
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
                # less negative than draftkings' home price -> better payout for home
                "home_american": -120,
                "away_american": 105,
                "snapshot_timestamp": "2026-08-12T14:00:00+00:00",
                "source": "the_odds_api",
            },
        ],
    )

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=0.05)]),  # favors home
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=odds_path,
    )

    assert result["best_price"] == {"bookmaker": "fanduel", "price": -120}


def test_kalshi_row_mixed_with_sportsbooks_renders_and_can_win_best_price(tmp_path):
    # PIPE-006 writes Kalshi rows into the same odds_books.jsonl schema every
    # sportsbook row already uses (bookmaker="kalshi", American-odds-
    # equivalent prices via MARKET-003's probability_to_american). Nothing in
    # _load_odds_books/_best_price_for_side special-cases bookmaker identity,
    # so a Kalshi row should be treated exactly like any other book: eligible
    # for implied-probability display and for winning "best price".
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(
        odds_path,
        [
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
                "bookmaker": "kalshi",
                # better payout for home than draftkings' -150
                "home_american": -110,
                "away_american": 105,
                "snapshot_timestamp": "2026-08-12T22:45:00+00:00",  # near-first-pitch, not the daily batch time
                "source": "kalshi",
            },
        ],
    )

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=0.05)]),  # favors home
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=odds_path,
    )

    books = {book["bookmaker"]: book for book in result["odds_books"]}
    assert set(books) == {"draftkings", "kalshi"}
    assert books["kalshi"]["implied_home_probability"] > 0
    assert books["kalshi"]["snapshot_pacific"]  # per-row formatting still works for its own timestamp

    # Kalshi offers the better home price here, so it should win best_price.
    assert result["best_price"] == {"bookmaker": "kalshi", "price": -110}


def test_kalshi_row_does_not_win_best_price_when_a_sportsbook_is_better(tmp_path):
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(
        odds_path,
        [
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "bookmaker": "kalshi",
                "home_american": -180,
                "away_american": 160,
                "snapshot_timestamp": "2026-08-12T22:45:00+00:00",
                "source": "kalshi",
            },
            {
                "run_date": "2026-08-12",
                "game_pk": 1,
                "bookmaker": "fanduel",
                "home_american": -120,
                "away_american": 105,
                "snapshot_timestamp": "2026-08-12T14:00:00+00:00",
                "source": "the_odds_api",
            },
        ],
    )

    result = load_game_detail(
        1,
        "2026-08-12",
        predictions_store=_FakeStore([_record(1, edge=0.05)]),  # favors home
        features_path=tmp_path / "game_features.jsonl",
        odds_books_path=odds_path,
    )

    assert result["best_price"] == {"bookmaker": "fanduel", "price": -120}


def test_odds_books_computes_implied_probability_per_book(tmp_path):
    # odds_books.jsonl is upserted by the writer (append_jsonl_records with
    # on_conflict="overwrite"), so realistic on-disk content has at most one
    # row per (run_date, game_pk, bookmaker) already -- no dedup needed here.
    odds_path = tmp_path / "odds_books.jsonl"
    _write_jsonl(
        odds_path,
        [
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
    assert books["draftkings"]["home_american"] == -150
    assert books["draftkings"]["implied_home_probability"] > 0
    assert books["draftkings"]["model_vs_book_delta"] == (
        0.55 - books["draftkings"]["implied_home_probability"]
    )
