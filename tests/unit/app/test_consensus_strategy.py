"""Unit tests for MARKET-005 consensus-confirmed shadow strategy helpers."""

from __future__ import annotations

import pytest

from app.consensus_strategy import (
    LARGE_MODEL_MARKET_DISAGREEMENT,
    MIN_MODEL_SIDE_PROBABILITY,
    MIN_SELECTED_SIDE_EDGE,
    MODEL_AWAY_MARKET_NEUTRAL,
    MODEL_MARKET_AGREE_AWAY,
    MODEL_MARKET_AGREE_HOME,
    MODEL_MARKET_DISAGREE,
    build_consensus_strategy_metrics,
    classify_model_market_relationship,
    consensus_confirmed_play,
    edge_selected_side,
    market_side,
    raw_model_side,
    selected_side_probabilities,
    strategy_metrics,
)


def _prediction(
    game_pk: int,
    *,
    p_home: float = 0.58,
    market_home: float | None = 0.54,
    edge: float | None = 0.04,
    timestamp: str = "2026-08-13T16:00:00+00:00",
    home_american: int | None = -120,
    away_american: int | None = 110,
) -> dict:
    row = {
        "game_pk": game_pk,
        "run_date": "2026-08-13",
        "model_probability": p_home,
        "prediction_timestamp": timestamp,
        "odds_snapshot_timestamp": "2026-08-13T15:55:00+00:00",
        "game_start_timestamp": "2026-08-14T02:05:00+00:00",
        "model_version": "v1",
        "home_american": home_american,
        "away_american": away_american,
    }
    if market_home is not None:
        row["market_probability"] = market_home
    if edge is not None:
        row["edge"] = edge
    return row


def _journal(prediction: dict, *, actual_home_win: bool = True) -> dict:
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "actual_home_win": actual_home_win,
        "enrichment_timestamp": "2026-08-14T07:00:00+00:00",
    }


def test_raw_market_and_edge_side_derivation_are_separate():
    crossover = _prediction(1, p_home=0.54, market_home=0.61, edge=-0.07)

    assert raw_model_side(crossover) == "HOME"
    assert market_side(crossover) == "HOME"
    assert edge_selected_side(crossover) == "AWAY"


def test_market_neutral_classification_uses_documented_band():
    row = _prediction(1, p_home=0.48, market_home=0.515, edge=-0.035)

    assert classify_model_market_relationship(row) == MODEL_AWAY_MARKET_NEUTRAL


def test_model_market_agreement_classification_for_home_and_away():
    home = _prediction(1, p_home=0.58, market_home=0.54, edge=0.04)
    away = _prediction(2, p_home=0.42, market_home=0.46, edge=-0.04)

    assert classify_model_market_relationship(home) == MODEL_MARKET_AGREE_HOME
    assert classify_model_market_relationship(away) == MODEL_MARKET_AGREE_AWAY


def test_large_disagreement_classification_is_not_plain_disagreement():
    plain = _prediction(1, p_home=0.53, market_home=0.47, edge=0.06)
    large = _prediction(2, p_home=0.60, market_home=0.47, edge=0.13)

    assert classify_model_market_relationship(plain) == MODEL_MARKET_DISAGREE
    assert classify_model_market_relationship(large) == LARGE_MODEL_MARKET_DISAGREEMENT


def test_large_disagreement_takes_precedence_over_agreement_and_neutral_buckets():
    same_direction = _prediction(1, p_home=0.70, market_home=0.60, edge=0.10)
    neutral_market = _prediction(2, p_home=0.42, market_home=0.515, edge=-0.095)

    assert classify_model_market_relationship(same_direction) == LARGE_MODEL_MARKET_DISAGREEMENT
    assert classify_model_market_relationship(neutral_market) == LARGE_MODEL_MARKET_DISAGREEMENT
    assert consensus_confirmed_play(same_direction) is False
    assert consensus_confirmed_play(neutral_market) is False


def test_home_and_away_selected_side_probability_transformations():
    home = selected_side_probabilities(_prediction(1, p_home=0.58, market_home=0.54))
    away = selected_side_probabilities(_prediction(2, p_home=0.42, market_home=0.46, edge=-0.04))

    assert home == {
        "selected_side": "HOME",
        "model_probability": pytest.approx(0.58),
        "market_probability": pytest.approx(0.54),
        "edge": pytest.approx(0.04),
    }
    assert away == {
        "selected_side": "AWAY",
        "model_probability": pytest.approx(0.58),
        "market_probability": pytest.approx(0.54),
        "edge": pytest.approx(0.04),
    }


def test_challenger_candidate_when_model_and_market_agree():
    row = _prediction(1, p_home=0.58, market_home=0.54, edge=0.04)

    assert consensus_confirmed_play(row) is True


def test_challenger_rejects_strong_market_contradiction():
    row = _prediction(1, p_home=0.60, market_home=0.47, edge=0.13)

    assert consensus_confirmed_play(row) is False


def test_challenger_rejects_low_model_probability_and_low_edge():
    low_probability = _prediction(
        1,
        p_home=MIN_MODEL_SIDE_PROBABILITY - 0.001,
        market_home=0.51,
        edge=0.039,
    )
    low_edge = _prediction(
        2,
        p_home=0.56,
        market_home=0.56 - MIN_SELECTED_SIDE_EDGE + 0.001,
        edge=MIN_SELECTED_SIDE_EDGE - 0.001,
    )

    assert consensus_confirmed_play(low_probability) is False
    assert consensus_confirmed_play(low_edge) is False


def test_missing_odds_and_missing_market_do_not_create_candidate():
    assert consensus_confirmed_play(_prediction(1, home_american=None)) is False
    assert consensus_confirmed_play(_prediction(2, market_home=None, edge=None)) is False


def test_stale_odds_age_blocks_consensus_candidate():
    stale = _prediction(
        1,
        timestamp="2026-08-13T20:30:00+00:00",
    )
    stale["odds_snapshot_timestamp"] = "2026-08-13T16:00:00+00:00"

    assert consensus_confirmed_play(stale) is False


def test_invalid_american_odds_are_unavailable_for_candidates_and_roi():
    invalid = _prediction(1, home_american=50)
    valid = _prediction(2, home_american=100)

    assert consensus_confirmed_play(invalid) is False

    metrics = strategy_metrics(
        [invalid, valid],
        {
            (invalid["game_pk"], invalid["prediction_timestamp"]): _journal(invalid),
            (valid["game_pk"], valid["prediction_timestamp"]): _journal(valid),
        },
        strategy="raw_model_favorite",
    )

    assert metrics["n_resolved"] == 2
    assert metrics["staked_units"] == 1
    assert metrics["average_odds"] == pytest.approx(100)
    assert metrics["roi"] == pytest.approx(1.0)


def test_pending_games_are_excluded_from_resolved_denominators():
    resolved = _prediction(1)
    pending = _prediction(2, timestamp="2026-08-13T17:00:00+00:00")

    metrics = build_consensus_strategy_metrics([resolved, pending], [_journal(resolved)])

    assert metrics["n"] == 2
    assert metrics["n_resolved"] == 1
    assert metrics["pending"] == 1
    assert metrics["wins"] + metrics["losses"] == 1


def test_latest_per_game_dedupe_uses_latest_prediction_only():
    first = _prediction(1, timestamp="2026-08-13T16:00:00+00:00")
    latest = _prediction(
        1,
        p_home=0.54,
        market_home=0.54,
        edge=0.0,
        timestamp="2026-08-13T17:00:00+00:00",
    )

    metrics = build_consensus_strategy_metrics([first, latest], [_journal(first)])

    assert metrics["n"] == 0
    assert metrics["n_resolved"] == 0


def test_empty_small_sample_behavior_returns_zero_counts():
    metrics = build_consensus_strategy_metrics([], [])

    assert metrics["n"] == 0
    assert metrics["win_rate"] is None
    assert metrics["roi"] is None
    assert sum(row["n"] for row in metrics["relationship_buckets"]) == 0


def test_roi_calculation_only_uses_rows_with_valid_odds():
    valid = _prediction(1, home_american=100)
    missing_odds = _prediction(2, home_american=None)

    metrics = strategy_metrics(
        [valid, missing_odds],
        {
            (valid["game_pk"], valid["prediction_timestamp"]): _journal(valid),
            (missing_odds["game_pk"], missing_odds["prediction_timestamp"]): _journal(missing_odds),
        },
        strategy="raw_model_favorite",
    )

    assert metrics["n_resolved"] == 2
    assert metrics["staked_units"] == 1
    assert metrics["roi"] == pytest.approx(1.0)
