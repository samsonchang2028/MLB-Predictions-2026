"""Tests for the ML-015 prospective diagnostic harness (`experiments.prospective_diagnostic`).

Six synthetic games exercise every dimension with explicit, hand-computed
membership: a raw-favorite/edge-sign "crossover" case (G3: model favors home
on raw probability but the edge flips negative, so the selection layer picks
away -- the exact probability-layer-vs-selection-layer distinction this task
must keep separate), a PASS row (G6, below the PLAY threshold), and a mix of
wins/losses. Covers: assembly correctness (explicit membership), regression
(deterministic output for fixed input), and that the 2026 holdout is never a
computation input (only a separate, read-only comparison).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from experiments.prospective_diagnostic import (
    build_report,
    historical_comparison,
)

_MODEL_VERSION = "test-v1"
_BASE = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)


def _daily(game_pk, hours_offset, hours_to_pitch, model_probability, market_probability, edge):
    prediction_timestamp = _BASE + timedelta(hours=hours_offset)
    return {
        "game_pk": game_pk,
        "home_team_id": 100,
        "away_team_id": 200,
        "model_probability": model_probability,
        "market_probability": market_probability,
        "edge": edge,
        "odds_snapshot_timestamp": prediction_timestamp,
        "model_version": _MODEL_VERSION,
        "prediction_timestamp": prediction_timestamp,
        "game_start_timestamp": prediction_timestamp + timedelta(hours=hours_to_pitch),
        "run_date": "2026-08-13",
        "home_american": -120,
        "away_american": 110,
    }


def _journal(game_pk, hours_offset, model_probability, actual_home_win, predicted_home_win):
    prediction_timestamp = _BASE + timedelta(hours=hours_offset)
    return {
        "game_pk": game_pk,
        "prediction_timestamp": prediction_timestamp,
        "model_version": _MODEL_VERSION,
        "build_id": "test-build",
        "model_probability": model_probability,
        "enrichment_timestamp": prediction_timestamp + timedelta(hours=6),
        "actual_home_win": actual_home_win,
        "predicted_home_win": predicted_home_win,
        "correct": predicted_home_win == actual_home_win,
        "home_score": 5 if actual_home_win else 2,
        "away_score": 2 if actual_home_win else 5,
    }


def _features(game_pk, starter_diff, bullpen_diff):
    return {
        "game_pk": game_pk,
        "build_id": "test-build",
        "run_date": "2026-08-13",
        "features": {
            "diff_starter_season_era_before": starter_diff,
            "diff_bullpen_bullpen_ip_L7": bullpen_diff,
        },
    }


# game_pk -> (hours_offset, hours_to_pitch, p_home, market_home, edge, actual_home_win,
#             predicted_home_win, starter_diff, bullpen_diff)
_GAMES = {
    1: (0, 3, 0.70, 0.60, 0.10, True, True, -2.0, -2.0),   # PLAY home, win, confident
    2: (0, 1, 0.30, 0.35, -0.05, False, False, 2.0, 2.0),  # PLAY away, win, confident
    3: (0, 10, 0.52, 0.60, -0.08, True, False, 0.0, 0.0),  # PLAY: raw favors home, edge flips to away -> LOSS
    4: (0, 30, 0.49, 0.555, -0.065, False, False, 1.0, -1.0),  # PLAY away, win, near-50
    5: (0, 4, 0.62, 0.59, 0.03, False, True, -1.0, 1.0),   # PLAY home, LOSS, confident
    6: (0, 15, 0.51, 0.505, 0.005, True, True, 1.5, -1.5),  # PASS, win, near-50
}


def _build_inputs():
    daily = [
        _daily(pk, off, ttp, p, mkt, edge) for pk, (off, ttp, p, mkt, edge, *_rest) in _GAMES.items()
    ]
    journal = [
        _journal(pk, off, p, actual, predicted)
        for pk, (off, _ttp, p, _mkt, _edge, actual, predicted, *_rest) in _GAMES.items()
    ]
    features = [
        _features(pk, starter_diff, bullpen_diff)
        for pk, (*_rest, starter_diff, bullpen_diff) in _GAMES.items()
    ]
    return daily, journal, features


def _report():
    daily, journal, features = _build_inputs()
    return build_report(
        daily_records=daily,
        journal_records=journal,
        game_features_records=features,
        skipped_records=[],
    )


# --------------------------------------------------------------------------- #
# Assembly correctness
# --------------------------------------------------------------------------- #
def test_part1_all_predictions_covers_every_resolved_game() -> None:
    report = _report()
    part1 = report["part1_all_predictions"]
    assert report["n_resolved_eligible_predictions"] == 6
    assert part1["n"] == 6
    assert part1["avg_predicted_home_probability"] == pytest.approx((0.70 + 0.30 + 0.52 + 0.49 + 0.62 + 0.51) / 6)
    assert part1["actual_home_win_rate"] == pytest.approx(3 / 6)  # G1, G3, G6 home wins
    assert part1["low_confidence"] is True  # n=6 < LOW_N_THRESHOLD


def test_part2_play_subset_excludes_pass_row_and_computes_win_rate() -> None:
    report = _report()
    part2 = report["part2_play_subset"]
    # G6 is PASS (abs edge 0.5% < 2%); the other 5 are PLAY.
    assert part2["n"] == 5
    # Wins: G1, G2, G4. Losses: G3 (crossover pick lost), G5.
    assert part2["wins"] == 3
    assert part2["losses"] == 2
    assert part2["win_rate"] == pytest.approx(0.6)
    assert part2["roi"] is None
    assert "stake" in part2["roi_note"] or "wager" in part2["roi_note"]


def test_part3_probability_buckets_use_raw_home_probability() -> None:
    buckets = _report()["part3_probability_buckets"]["buckets"]
    assert buckets["<45%"]["n"] == 1  # G2 (0.30)
    assert buckets["45-50%"]["n"] == 1  # G4 (0.49)
    assert buckets["50-55%"]["n"] == 2  # G3 (0.52), G6 (0.51)
    assert buckets["60-65%"]["n"] == 1  # G5 (0.62)
    assert buckets[">=65%"]["n"] == 1  # G1 (0.70)
    assert "55-60%" not in buckets  # no game landed there; not fabricated


def test_part4_edge_buckets_group_by_absolute_edge_over_all_resolved_rows() -> None:
    buckets = _report()["part4_edge_buckets"]
    assert buckets["0-2%"]["n"] == 1  # G6 (0.5%)
    assert buckets["2-4%"]["n"] == 1  # G5 (3%)
    assert buckets["4-6%"]["n"] == 1  # G2 (5%)
    assert buckets["6-8%"]["n"] == 1  # G4 (6.5%)
    assert buckets["8%+"]["n"] == 2  # G1 (10%), G3 (8%)
    assert buckets["8%+"]["actual_win_rate"] == pytest.approx(0.5)  # G1 win, G3 loss
    assert buckets["8%+"]["roi"] is None


def test_part5_favorite_underdog_uses_raw_probability_not_selection_side() -> None:
    dims = _report()["part5_failure_regimes"]["dimensions"]
    favorite = dims["favorite_underdog"]["buckets"]
    assert favorite["model_favors_home"]["n"] == 4  # G1, G3, G5, G6 (raw p >= 0.5)
    assert favorite["model_favors_away"]["n"] == 2  # G2, G4


def test_part5_home_away_selection_diverges_from_favorite_on_the_crossover_game() -> None:
    """G3: raw p=0.52 favors home, but edge=-0.08 flips the PLAY pick to away.
    This dimension must reflect the actual selection side, not the raw favorite."""
    dims = _report()["part5_failure_regimes"]["dimensions"]
    selection = dims["home_away_selection"]["buckets"]
    assert selection["home"]["n"] == 3  # G1, G5, G6
    assert selection["away"]["n"] == 3  # G2, G3, G4 (G3 moved here despite raw home favorite)


def test_part5_near_50_vs_confident() -> None:
    dims = _report()["part5_failure_regimes"]["dimensions"]
    near = dims["near_50_vs_confident"]["buckets"]
    assert near["near_50"]["n"] == 3  # G3 (.52), G4 (.49), G6 (.51)
    assert near["confident"]["n"] == 3  # G1 (.70), G2 (.30), G5 (.62)


def test_part5_high_edge_vs_low_edge_matches_play_pass_threshold() -> None:
    dims = _report()["part5_failure_regimes"]["dimensions"]
    edge_dim = dims["high_edge_vs_low_edge"]["buckets"]
    assert edge_dim["high_edge_play"]["n"] == 5
    assert edge_dim["low_edge_pass"]["n"] == 1


def test_part5_starter_and_bullpen_terciles_split_evenly() -> None:
    dims = _report()["part5_failure_regimes"]["dimensions"]
    starter = dims["starter_quality_regime"]["buckets"]
    assert starter["low_tercile"]["n"] == 2  # G1, G5
    assert starter["mid_tercile"]["n"] == 2  # G3, G4
    assert starter["high_tercile"]["n"] == 2  # G2, G6

    bullpen = dims["bullpen_workload_regime"]["buckets"]
    assert bullpen["low_tercile"]["n"] == 2  # G1, G6
    assert bullpen["mid_tercile"]["n"] == 2  # G3, G4
    assert bullpen["high_tercile"]["n"] == 2  # G2, G5


def test_part5_time_to_first_pitch_buckets() -> None:
    dims = _report()["part5_failure_regimes"]["dimensions"]
    ttfp = dims["time_to_first_pitch"]["buckets"]
    assert ttfp["<2h"]["n"] == 1  # G2
    assert ttfp["2-6h"]["n"] == 2  # G1, G5
    assert ttfp["6-24h"]["n"] == 2  # G3, G6
    assert ttfp["24h+"]["n"] == 1  # G4


def test_part5_skips_sportsbook_and_starter_uncertainty_with_stated_reasons() -> None:
    skipped = _report()["part5_failure_regimes"]["skipped_dimensions"]
    assert "sportsbook" in skipped
    assert "starter_uncertainty_missing_late_starter" in skipped
    assert skipped["sportsbook"]  # non-empty stated reason
    assert skipped["starter_uncertainty_missing_late_starter"]


def test_model_version_filter_excludes_other_model_versions() -> None:
    daily, journal, features = _build_inputs()
    daily[0] = {**daily[0], "model_version": "some-other-model"}
    journal[0] = {**journal[0], "model_version": "some-other-model"}
    report = build_report(
        daily_records=daily,
        journal_records=journal,
        game_features_records=features,
        model_version=_MODEL_VERSION,
    )
    assert report["n_resolved_eligible_predictions"] == 5


def test_data_quality_counts_malformed_rows_and_starter_skips() -> None:
    daily, journal, features = _build_inputs()
    daily.append({"game_pk": 999, "model_version": _MODEL_VERSION})  # missing required fields
    skipped = [{"game_pk": 500, "reason": "no_starter_announced"}, {"game_pk": 501, "reason": "other"}]
    report = build_report(
        daily_records=daily,
        journal_records=journal,
        game_features_records=features,
        skipped_records=skipped,
    )
    assert report["data_quality"]["daily_board_malformed_skips"] == 1
    assert report["data_quality"]["starter_uncertainty_skip_count"] == 1
    # The malformed row must not silently count as a real resolved prediction.
    assert report["n_resolved_eligible_predictions"] == 6


# --------------------------------------------------------------------------- #
# Regression (deterministic output)
# --------------------------------------------------------------------------- #
def test_build_report_is_deterministic_for_a_fixed_snapshot() -> None:
    assert _report() == _report()


# --------------------------------------------------------------------------- #
# 2026 holdout is comparison-only, never a computation input
# --------------------------------------------------------------------------- #
def test_build_report_never_accepts_holdout_data() -> None:
    params = inspect.signature(build_report).parameters
    assert not any("holdout" in name for name in params)


def test_historical_comparison_is_read_only_and_does_not_mutate_part1() -> None:
    part1 = {"log_loss": 0.70, "brier": 0.25, "ece": 0.05, "roc_auc": 0.55, "accuracy": 0.55}
    part1_snapshot = dict(part1)
    holdout_report = {
        "metrics": {
            "log_loss": 0.6888,
            "brier": 0.2478,
            "ece": 0.0222,
            "n_test": 1797,
            "secondary": {"roc_auc": 0.5497, "accuracy": 0.5381},
        }
    }
    result = historical_comparison(part1, holdout_report)

    assert part1 == part1_snapshot  # not mutated
    assert result["n_holdout"] == 1797
    assert result["metrics"]["log_loss"]["delta"] == pytest.approx(0.70 - 0.6888)
    assert result["metrics"]["roc_auc"]["holdout_2026"] == pytest.approx(0.5497)
