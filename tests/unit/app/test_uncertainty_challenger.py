"""Unit tests for MARKET-004 uncertainty-adjusted challenger helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.board import DEFAULT_EDGE_THRESHOLD
from app.uncertainty_challenger import (
    MIN_BUCKET_N,
    build_shadow_strategy_comparison,
    build_uncertainty_profile,
    favorite_edge,
    percentage_points,
    prepare_uncertainty_candidate_row,
    prepare_uncertainty_candidate_rows,
    probability_bucket,
    raw_model_favorite_home,
    selected_market_probability,
    selected_model_probability,
    wilson_interval,
    wins_per_100,
)


def _prediction(
    game_pk: int,
    *,
    p_home: float = 0.58,
    market_home: float = 0.52,
    edge: float = 0.06,
    timestamp: str = "2026-08-13T16:00:00+00:00",
    home_american: int | None = -120,
    away_american: int | None = 110,
) -> dict:
    return {
        "game_pk": game_pk,
        "run_date": "2026-08-13",
        "matchup": "BOS @ NYY",
        "home_team": "NYY",
        "away_team": "BOS",
        "model_probability": p_home,
        "market_probability": market_home,
        "edge": edge,
        "prediction_timestamp": timestamp,
        "odds_snapshot_timestamp": "2026-08-13T15:55:00+00:00",
        "game_start_timestamp": "2026-08-14T02:05:00+00:00",
        "game_start_pacific": "2026-08-13 19:05 PDT",
        "model_version": "v1",
        "home_american": home_american,
        "away_american": away_american,
        "recommendation": "PLAY NYY" if abs(edge) >= 0.02 else "PASS",
    }


def _journal(prediction: dict, *, actual_home_win: bool = True) -> dict:
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "actual_home_win": actual_home_win,
        "enrichment_timestamp": "2026-08-14T07:00:00+00:00",
    }


def _profile_row(*, n: int = MIN_BUCKET_N, lower: float = 0.54, upper: float = 0.66) -> dict:
    return {"bucket": "55-60%", "n": n, "wins": 18, "lower": lower, "upper": upper, "low_sample": n < MIN_BUCKET_N}


def test_raw_model_favorite_and_selected_probabilities_home_and_away():
    home = _prediction(1, p_home=0.58, market_home=0.52)
    away = _prediction(2, p_home=0.44, market_home=0.49, edge=-0.05)

    assert raw_model_favorite_home(home) is True
    assert selected_model_probability(home) == pytest.approx(0.58)
    assert selected_market_probability(home) == pytest.approx(0.52)
    assert favorite_edge(home) == pytest.approx(0.06)

    assert raw_model_favorite_home(away) is False
    assert selected_model_probability(away) == pytest.approx(0.56)
    assert selected_market_probability(away) == pytest.approx(0.51)
    assert favorite_edge(away) == pytest.approx(0.05)


def test_display_formatting_uses_wins_per_100_and_percentage_points():
    assert wins_per_100(0.583) == "58.3"
    assert wins_per_100(None) == "-"
    assert percentage_points(0.042) == "+4.2 pp"
    assert percentage_points(-0.011) == "-1.1 pp"


def test_wilson_interval_returns_conservative_bounds():
    lower, upper = wilson_interval(18, 30)
    assert lower == pytest.approx(0.4232, abs=0.0001)
    assert upper == pytest.approx(0.7541, abs=0.0001)


def test_probability_bucket_boundaries():
    assert probability_bucket(0.50) == "50-55%"
    assert probability_bucket(0.5499) == "50-55%"
    assert probability_bucket(0.55) == "55-60%"
    assert probability_bucket(0.60) == "60-65%"
    assert probability_bucket(0.65) == "65-70%"
    assert probability_bucket(0.70) == "70%+"
    assert probability_bucket(0.49) is None


def test_low_sample_blocks_challenger_promotion():
    row = prepare_uncertainty_candidate_row(
        _prediction(1),
        uncertainty_profile={"55-60%": _profile_row(n=29, lower=0.56)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in row["risk_flags"]


def test_lower_bound_edge_and_ev_can_promote_candidate_when_odds_exist():
    row = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52, home_american=100),
        uncertainty_profile={"55-60%": _profile_row(lower=0.54, upper=0.64)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert row["conservative_edge"] == pytest.approx(0.02)
    assert row["lower_bound_ev"] == pytest.approx(0.08)
    assert row["challenger_label"] == "CANDIDATE"


def test_lower_bound_edge_failure_is_flagged():
    row = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52, home_american=100),
        uncertainty_profile={"55-60%": _profile_row(lower=0.525)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert row["challenger_label"] == "WATCH"
    assert "EDGE_DOES_NOT_SURVIVE_UNCERTAINTY" in row["risk_flags"]


def test_missing_and_stale_odds_are_flagged():
    row = _prediction(1, home_american=None)
    row["odds_snapshot_timestamp"] = "2026-08-13T08:00:00+00:00"

    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"55-60%": _profile_row(lower=0.56)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert "MISSING_ODDS" in candidate["risk_flags"]
    assert "STALE_ODDS" in candidate["risk_flags"]
    assert candidate["lower_bound_ev"] is None


def test_baseline_play_against_raw_model_favorite_is_flagged():
    row = _prediction(1, p_home=0.54, market_home=0.60, edge=-0.06)

    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"50-55%": {"n": 30, "lower": 0.51, "upper": 0.64, "low_sample": False}},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert "BASELINE_PLAY_AGAINST_MODEL_FAVORITE" in candidate["risk_flags"]


def test_empty_input_profile_returns_zeroed_buckets():
    profile = build_uncertainty_profile([], [])
    assert sum(row["n"] for row in profile.values()) == 0
    assert all(row["low_sample"] for row in profile.values())
    assert prepare_uncertainty_candidate_rows([]) == []


def test_profile_excludes_pending_and_dedupes_to_latest_prediction_per_game():
    first = _prediction(1, p_home=0.58, timestamp="2026-08-13T16:00:00+00:00")
    latest = _prediction(1, p_home=0.62, timestamp="2026-08-13T18:00:00+00:00")
    pending = _prediction(2, p_home=0.58, timestamp="2026-08-13T19:00:00+00:00")

    profile = build_uncertainty_profile(
        [first, latest, first, pending],
        [_journal(first), _journal(latest, actual_home_win=False)],
    )

    assert profile["55-60%"]["n"] == 0
    assert profile["60-65%"]["n"] == 1
    assert profile["60-65%"]["wins"] == 0


def test_raw_model_favorite_at_even_probability_is_home():
    row = _prediction(1, p_home=0.5, market_home=0.48)
    assert raw_model_favorite_home(row) is True
    assert selected_model_probability(row) == pytest.approx(0.5)
    assert selected_market_probability(row) == pytest.approx(0.48)
    assert favorite_edge(row) == pytest.approx(0.02)


def test_favorite_edge_uses_model_favorite_not_stored_edge_sign():
    row = _prediction(1, p_home=0.58, market_home=0.52, edge=-0.06)
    assert favorite_edge(row) == pytest.approx(0.06)
    assert raw_model_favorite_home(row) is True


def test_baseline_play_home_against_away_model_favorite_is_flagged():
    row = _prediction(1, p_home=0.44, market_home=0.38, edge=0.06)

    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"50-55%": {"n": 30, "lower": 0.51, "upper": 0.64, "low_sample": False}},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )

    assert raw_model_favorite_home(row) is False
    assert "BASELINE_PLAY_AGAINST_MODEL_FAVORITE" in candidate["risk_flags"]


def test_pending_journal_rows_are_excluded_from_resolved_profile():
    resolved = _prediction(1, p_home=0.58)
    pending = _prediction(2, p_home=0.58)
    pending_journal = _journal(pending)
    pending_journal["actual_home_win"] = None

    profile = build_uncertainty_profile(
        [resolved, pending],
        [_journal(resolved, actual_home_win=True), pending_journal],
    )

    assert profile["55-60%"]["n"] == 1
    assert profile["55-60%"]["wins"] == 1


def test_older_resolved_snapshot_is_not_used_when_latest_is_pending():
    first = _prediction(1, p_home=0.58, timestamp="2026-08-13T16:00:00+00:00")
    latest = _prediction(1, p_home=0.62, timestamp="2026-08-13T18:00:00+00:00")

    profile = build_uncertainty_profile([first, latest], [_journal(first)])

    assert sum(row["n"] for row in profile.values()) == 0


def test_duplicate_snapshots_do_not_double_count_resolved_games():
    early_a = _prediction(10, p_home=0.58, timestamp="2026-08-13T16:00:00+00:00")
    late_a = _prediction(10, p_home=0.58, timestamp="2026-08-13T18:00:00+00:00")
    early_b = _prediction(11, p_home=0.61, timestamp="2026-08-13T16:00:00+00:00")
    late_b = _prediction(11, p_home=0.61, timestamp="2026-08-13T18:00:00+00:00")

    profile = build_uncertainty_profile(
        [early_a, late_a, early_a, early_b, late_b],
        [
            _journal(early_a, actual_home_win=True),
            _journal(late_a, actual_home_win=True),
            _journal(early_b, actual_home_win=False),
            _journal(late_b, actual_home_win=False),
        ],
    )

    assert profile["55-60%"]["n"] == 1
    assert profile["55-60%"]["wins"] == 1
    assert profile["60-65%"]["n"] == 1
    assert profile["60-65%"]["wins"] == 0
    assert sum(row["n"] for row in profile.values()) == 2


def test_away_favorite_win_counts_when_home_loses():
    row = _prediction(1, p_home=0.44, market_home=0.49, edge=-0.05)
    profile = build_uncertainty_profile([row], [_journal(row, actual_home_win=False)])
    assert profile["55-60%"]["n"] == 1
    assert profile["55-60%"]["wins"] == 1


def test_bucket_n_below_thirty_cannot_promote_even_with_strong_lower_bound():
    row = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52, home_american=100),
        uncertainty_profile={"55-60%": _profile_row(n=29, lower=0.70, upper=0.80)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in row["risk_flags"]


def test_bucket_n_at_thirty_can_promote_when_other_gates_pass():
    row = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52, home_american=100),
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert row["challenger_label"] == "CANDIDATE"
    assert "LOW_SAMPLE_UNCERTAINTY" not in row["risk_flags"]


def test_missing_odds_blocks_promotion_and_omits_lower_bound_ev():
    candidate = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52, home_american=None, away_american=None),
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.56, upper=0.66)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate["lower_bound_ev"] is None
    assert candidate["challenger_label"] == "WATCH"
    assert "MISSING_ODDS" in candidate["risk_flags"]


def test_wilson_interval_rejects_empty_and_invalid_counts():
    assert wilson_interval(0, 0) is None
    with pytest.raises(ValueError):
        wilson_interval(2, 1)


def test_baseline_play_threshold_constant_is_unchanged():
    assert DEFAULT_EDGE_THRESHOLD == 0.02
    now = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)
    play_against = _prediction(1, p_home=0.54, market_home=0.56, edge=-0.02)
    just_below = _prediction(1, p_home=0.54, market_home=0.559, edge=-0.019)
    play_flags = prepare_uncertainty_candidate_row(
        play_against, uncertainty_profile={}, now=now
    )["risk_flags"]
    below_flags = prepare_uncertainty_candidate_row(
        just_below, uncertainty_profile={}, now=now
    )["risk_flags"]
    assert "BASELINE_PLAY_AGAINST_MODEL_FAVORITE" in play_flags
    assert "BASELINE_PLAY_AGAINST_MODEL_FAVORITE" not in below_flags


def test_prepare_candidate_row_handles_omitted_uncertainty_profile():
    row = prepare_uncertainty_candidate_row(
        _prediction(1),
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in row["risk_flags"]
    assert row["similar_games_wins_per_100"] == "unavailable (low sample)"
    assert row["uncertainty_range_per_100"] == "unavailable (low sample)"


def test_away_favorite_uses_away_odds_and_selected_side_edge():
    row = _prediction(1, p_home=0.44, market_home=0.49, edge=-0.05, home_american=-120, away_american=105)
    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.56, upper=0.66)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert raw_model_favorite_home(row) is False
    assert favorite_edge(row) == pytest.approx(0.05)
    assert candidate["offered_odds"] == "+105"


def test_low_sample_range_display_is_unavailable():
    row = prepare_uncertainty_candidate_row(
        _prediction(1),
        uncertainty_profile={"55-60%": _profile_row(n=29, lower=0.56, upper=0.66)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert row["uncertainty_range_per_100"] == "unavailable (low sample)"
    assert row["similar_games_wins_per_100"] == "unavailable (low sample)"


def test_game_already_started_blocks_candidate():
    row = _prediction(1, p_home=0.58, market_home=0.52, home_american=100)
    row["game_start_timestamp"] = "2026-08-13T15:00:00+00:00"
    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate["challenger_label"] == "WATCH"
    assert "GAME_ALREADY_STARTED" in candidate["risk_flags"]


def test_game_starting_soon_blocks_candidate():
    row = _prediction(1, p_home=0.58, market_home=0.52, home_american=100)
    row["game_start_timestamp"] = "2026-08-13T16:20:00+00:00"
    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert candidate["challenger_label"] == "WATCH"
    assert "GAME_STARTING_SOON" in candidate["risk_flags"]


def test_similar_games_uses_bucket_empirical_rate_when_sample_sufficient():
    row = prepare_uncertainty_candidate_row(
        _prediction(1, p_home=0.58, market_home=0.52),
        uncertainty_profile={
            "55-60%": {"n": 30, "wins": 18, "win_rate": 0.60, "lower": 0.54, "upper": 0.66, "low_sample": False}
        },
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
    )
    assert row["similar_games_wins_per_100"] == "60.0"
    assert row["model_wins_per_100"] == "58.0"
    assert row["uncertainty_range_per_100"] == "54.0-66.0"


def test_shadow_strategy_comparison_excludes_pending_from_resolved_metrics():
    resolved = _prediction(1, p_home=0.58, edge=0.06)
    pending = _prediction(2, p_home=0.62, edge=0.05, timestamp="2026-08-13T17:00:00+00:00")
    comparison = build_shadow_strategy_comparison(
        [resolved, pending],
        [_journal(resolved, actual_home_win=True)],
    )
    baseline = comparison["strategies"]["baseline_play"]
    assert baseline["wins"] + baseline["losses"] <= baseline["n"]
    assert baseline["pending"] >= 0
    assert comparison["window_label"].startswith("Prospective daily.jsonl + journal")
    assert comparison["strategies"]["baseline_play"]["n_resolved"] == (
        comparison["strategies"]["baseline_play"]["wins"]
        + comparison["strategies"]["baseline_play"]["losses"]
    )


def _resolved_historical_prediction(
    game_pk: int,
    *,
    p_home: float = 0.58,
    market_home: float = 0.52,
    edge: float = 0.06,
    actual_home_win: bool = True,
    timestamp: str = "2026-08-01T16:00:00+00:00",
    odds_snapshot_timestamp: str | None = None,
) -> tuple[dict, dict]:
    """Finished-game fixture with fresh pregame odds (minutes before prediction)."""
    prediction = _prediction(
        game_pk,
        p_home=p_home,
        market_home=market_home,
        edge=edge,
        timestamp=timestamp,
        home_american=100,
    )
    pred_dt = datetime.fromisoformat(timestamp)
    prediction["game_start_timestamp"] = pred_dt.replace(hour=23, minute=0, second=0).isoformat()
    if odds_snapshot_timestamp is None:
        prediction["odds_snapshot_timestamp"] = (pred_dt - timedelta(minutes=5)).isoformat()
    else:
        prediction["odds_snapshot_timestamp"] = odds_snapshot_timestamp
    journal = _journal(prediction, actual_home_win=actual_home_win)
    journal["enrichment_timestamp"] = (pred_dt + timedelta(days=1)).replace(
        hour=7, minute=0, second=0
    ).isoformat()
    return prediction, journal


def _bucket_filler_predictions(
    start_pk: int,
    count: int,
    *,
    wins: int,
    first_timestamp: str = "2026-07-01T16:00:00+00:00",
) -> tuple[list[dict], list[dict]]:
    predictions: list[dict] = []
    journal: list[dict] = []
    first = datetime.fromisoformat(first_timestamp)
    for offset in range(count):
        game_pk = start_pk + offset
        timestamp = (first + timedelta(days=offset)).isoformat()
        prediction, entry = _resolved_historical_prediction(
            game_pk,
            actual_home_win=offset < wins,
            timestamp=timestamp,
        )
        predictions.append(prediction)
        journal.append(entry)
    return predictions, journal


def test_shadow_comparison_selects_challenger_on_finished_game_without_wall_clock_flags():
    fillers, filler_journal = _bucket_filler_predictions(100, MIN_BUCKET_N, wins=22)
    target, target_journal = _resolved_historical_prediction(200)
    comparison = build_shadow_strategy_comparison(
        fillers + [target],
        filler_journal + [target_journal],
    )
    challenger = comparison["strategies"]["uncertainty_challenger"]
    assert challenger["n"] >= 1
    assert challenger["wins"] + challenger["losses"] >= 1


def test_backtest_candidate_ignores_self_contribution_in_bucket():
    fillers, filler_journal = _bucket_filler_predictions(100, MIN_BUCKET_N - 1, wins=21)
    target, target_journal = _resolved_historical_prediction(200)
    predictions = fillers + [target]
    journal = filler_journal + [target_journal]

    with_self = build_uncertainty_profile(predictions, journal)
    without_self = build_uncertainty_profile(predictions, journal, exclude_game_pk=200)
    assert with_self["55-60%"]["n"] == MIN_BUCKET_N
    assert without_self["55-60%"]["n"] == MIN_BUCKET_N - 1

    inflated = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=with_self,
        evaluation_mode="backtest",
    )
    backtest_row = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=without_self,
        evaluation_mode="backtest",
    )
    live_row = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=with_self,
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
        evaluation_mode="live",
    )
    assert inflated["challenger_label"] == "CANDIDATE"
    assert backtest_row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in backtest_row["risk_flags"]
    assert live_row["challenger_label"] == "WATCH"
    assert "GAME_ALREADY_STARTED" in live_row["risk_flags"]

    comparison = build_shadow_strategy_comparison(predictions, journal)
    assert comparison["strategies"]["uncertainty_challenger"]["n"] == 0


def test_live_mode_still_blocks_game_already_started():
    row = _prediction(1, p_home=0.58, market_home=0.52, home_american=100)
    row["game_start_timestamp"] = "2026-08-13T15:00:00+00:00"
    candidate = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile={"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)},
        now=datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc),
        evaluation_mode="live",
    )
    assert candidate["challenger_label"] == "WATCH"
    assert "GAME_ALREADY_STARTED" in candidate["risk_flags"]


def test_backtest_mode_skips_wall_clock_started_flags_but_not_decision_time_stale_odds():
    row, _ = _resolved_historical_prediction(1)
    profile = {"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)}
    now = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)
    backtest = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile=profile,
        now=now,
        evaluation_mode="backtest",
    )
    live = prepare_uncertainty_candidate_row(
        row,
        uncertainty_profile=profile,
        now=now,
        evaluation_mode="live",
    )
    assert backtest["challenger_label"] == "CANDIDATE"
    assert "GAME_ALREADY_STARTED" not in backtest["risk_flags"]
    assert "GAME_STARTING_SOON" not in backtest["risk_flags"]
    assert "STALE_ODDS" not in backtest["risk_flags"]
    assert live["challenger_label"] == "WATCH"
    assert "GAME_ALREADY_STARTED" in live["risk_flags"]
    assert "STALE_ODDS" in live["risk_flags"]


def test_backtest_stale_odds_uses_prediction_timestamp_live_uses_wall_clock():
    profile = {"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)}
    stale_at_pred, _ = _resolved_historical_prediction(
        1,
        odds_snapshot_timestamp="2026-08-01T08:00:00+00:00",
    )
    shortly_after_odds = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    backtest_stale = prepare_uncertainty_candidate_row(
        stale_at_pred,
        uncertainty_profile=profile,
        now=shortly_after_odds,
        evaluation_mode="backtest",
    )
    live_wall_clock_fresh = prepare_uncertainty_candidate_row(
        stale_at_pred,
        uncertainty_profile=profile,
        now=shortly_after_odds,
        evaluation_mode="live",
    )
    assert backtest_stale["challenger_label"] == "WATCH"
    assert "STALE_ODDS" in backtest_stale["risk_flags"]
    assert "STALE_ODDS" not in live_wall_clock_fresh["risk_flags"]

    fresh_at_pred, _ = _resolved_historical_prediction(2)
    later_now = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)
    backtest_fresh = prepare_uncertainty_candidate_row(
        fresh_at_pred,
        uncertainty_profile=profile,
        now=later_now,
        evaluation_mode="backtest",
    )
    live_wall_clock_stale = prepare_uncertainty_candidate_row(
        fresh_at_pred,
        uncertainty_profile=profile,
        now=later_now,
        evaluation_mode="live",
    )
    assert backtest_fresh["challenger_label"] == "CANDIDATE"
    assert "STALE_ODDS" not in backtest_fresh["risk_flags"]
    assert "STALE_ODDS" in live_wall_clock_stale["risk_flags"]


def test_later_games_cannot_lift_early_game_over_wilson_n_gate():
    target, target_journal = _resolved_historical_prediction(
        1, timestamp="2026-07-01T16:00:00+00:00"
    )
    later, later_journal = _bucket_filler_predictions(
        100, MIN_BUCKET_N, wins=22, first_timestamp="2026-07-02T16:00:00+00:00"
    )
    predictions = [target] + later
    journal = [target_journal] + later_journal
    as_of = datetime(2026, 7, 1, 16, tzinfo=timezone.utc)

    as_of_profile = build_uncertainty_profile(
        predictions, journal, exclude_game_pk=1, as_of_timestamp=as_of
    )
    leaked_profile = build_uncertainty_profile(predictions, journal, exclude_game_pk=1)
    assert as_of_profile["55-60%"]["n"] == 0
    assert leaked_profile["55-60%"]["n"] == MIN_BUCKET_N

    as_of_row = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=as_of_profile,
        evaluation_mode="backtest",
    )
    leaked_row = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=leaked_profile,
        evaluation_mode="backtest",
    )
    assert as_of_row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in as_of_row["risk_flags"]
    assert leaked_row["challenger_label"] == "CANDIDATE"

    selected_pks = [
        row["game_pk"]
        for row in predictions
        if prepare_uncertainty_candidate_row(
            row,
            uncertainty_profile=build_uncertainty_profile(
                predictions,
                journal,
                exclude_game_pk=row["game_pk"],
                as_of_timestamp=datetime.fromisoformat(row["prediction_timestamp"]),
            ),
            evaluation_mode="backtest",
        )["challenger_label"]
        == "CANDIDATE"
    ]
    assert 1 not in selected_pks


def test_own_resolved_win_does_not_lift_wilson_gate_in_shadow_backtest():
    """21/30 similar games miss the 1pp lower-bound gate; including this win would pass."""
    fillers, filler_journal = _bucket_filler_predictions(100, MIN_BUCKET_N, wins=21)
    target, target_journal = _resolved_historical_prediction(200, actual_home_win=True)
    predictions = fillers + [target]
    journal = filler_journal + [target_journal]

    with_self = build_uncertainty_profile(predictions, journal)
    without_self = build_uncertainty_profile(predictions, journal, exclude_game_pk=200)
    assert with_self["55-60%"]["n"] == MIN_BUCKET_N + 1
    assert without_self["55-60%"]["n"] == MIN_BUCKET_N

    inflated = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=with_self,
        evaluation_mode="backtest",
    )
    leave_one_out = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=without_self,
        evaluation_mode="backtest",
    )
    assert inflated["challenger_label"] == "CANDIDATE"
    assert leave_one_out["challenger_label"] == "WATCH"
    assert "EDGE_DOES_NOT_SURVIVE_UNCERTAINTY" in leave_one_out["risk_flags"]

    selected_pks = [
        row["game_pk"]
        for row in predictions
        if prepare_uncertainty_candidate_row(
            row,
            uncertainty_profile=build_uncertainty_profile(
                predictions, journal, exclude_game_pk=row["game_pk"]
            ),
            evaluation_mode="backtest",
        )["challenger_label"]
        == "CANDIDATE"
    ]
    assert 200 not in selected_pks


def test_backtest_skips_starting_soon_while_live_flags_it():
    row = _prediction(1, p_home=0.58, market_home=0.52, home_american=100)
    row["game_start_timestamp"] = "2026-08-13T16:20:00+00:00"
    profile = {"55-60%": _profile_row(n=30, lower=0.54, upper=0.64)}
    now = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)
    backtest = prepare_uncertainty_candidate_row(
        row, uncertainty_profile=profile, now=now, evaluation_mode="backtest"
    )
    live = prepare_uncertainty_candidate_row(
        row, uncertainty_profile=profile, now=now, evaluation_mode="live"
    )
    assert backtest["challenger_label"] == "CANDIDATE"
    assert "GAME_STARTING_SOON" not in backtest["risk_flags"]
    assert "GAME_ALREADY_STARTED" not in backtest["risk_flags"]
    assert live["challenger_label"] == "WATCH"
    assert "GAME_STARTING_SOON" in live["risk_flags"]


def test_as_of_wilson_excludes_not_yet_started_games_even_if_predicted_earlier():
    """Nightcap predicted in the morning must not enter an afternoon game's as-of bucket."""
    fillers, filler_journal = _bucket_filler_predictions(
        100, MIN_BUCKET_N - 1, wins=21, first_timestamp="2026-06-01T16:00:00+00:00"
    )
    nightcap, nightcap_journal = _resolved_historical_prediction(
        50, timestamp="2026-07-01T10:00:00+00:00"
    )
    nightcap["game_start_timestamp"] = "2026-07-01T23:00:00+00:00"
    target, target_journal = _resolved_historical_prediction(
        1, timestamp="2026-07-01T16:00:00+00:00"
    )
    target["game_start_timestamp"] = "2026-07-01T17:05:00+00:00"
    predictions = fillers + [nightcap, target]
    journal = filler_journal + [nightcap_journal, target_journal]
    as_of = datetime.fromisoformat(target["prediction_timestamp"])

    as_of_profile = build_uncertainty_profile(
        predictions, journal, exclude_game_pk=1, as_of_timestamp=as_of
    )
    assert as_of_profile["55-60%"]["n"] == MIN_BUCKET_N - 1

    as_of_row = prepare_uncertainty_candidate_row(
        target,
        uncertainty_profile=as_of_profile,
        evaluation_mode="backtest",
    )
    assert as_of_row["challenger_label"] == "WATCH"
    assert "LOW_SAMPLE_UNCERTAINTY" in as_of_row["risk_flags"]
