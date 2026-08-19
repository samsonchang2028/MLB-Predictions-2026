"""Tests for the ML-013 failure-regime harness (`experiments.failure_regimes`).

Mirrors `tests/unit/experiments/test_ablation.py`'s three required categories
on a small synthetic matrix: assembly correctness (explicit slice membership),
leakage (mutating future-fold data must not change an earlier fold's slice
metrics), and regression (deterministic output for a fixed input).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from experiments.failure_regimes import (
    LOW_N_THRESHOLD,
    _edge_bucket_label,
    _probability_bucket_label,
    compute_failure_regimes,
    slice_by,
    tercile_labels,
)


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


_ALL_COLUMNS = (
    "home_team_win_pct_L7",
    "away_team_win_pct_L7",
    "diff_team_win_pct_L7",
    "diff_starter_season_era_before",
    "diff_bullpen_bullpen_ip_L7",
)


def _synthetic_matrix(per_season: int = 40, seed: int = 0, include_holdout: bool = False) -> dict:
    """A learnable dev matrix (2021-2025, optionally +2026), real column names.

    Only ``home_team_win_pct_L7`` carries signal; the starter/bullpen diff
    columns are independent noise, used only to exercise the regime-bucketing
    machinery (not to draw feature-value conclusions -- that needs the real
    certified build, run separately via the operator script).
    """
    rng = np.random.default_rng(seed)
    seasons = (2021, 2022, 2023, 2024, 2025)
    if include_holdout:
        seasons = seasons + (2026,)
    rows = []
    game_pk = 9000
    for season in seasons:
        for _ in range(per_season):
            signal = float(rng.normal())
            prob = 1.0 / (1.0 + np.exp(-signal))
            home_win = bool(rng.random() < prob)
            features = {col: float(rng.normal()) for col in _ALL_COLUMNS}
            features["home_team_win_pct_L7"] = signal
            rows.append(
                {
                    "game_pk": game_pk,
                    "game_date": _dt(season, day=(game_pk % 27) + 1),
                    "prediction_timestamp": _dt(season),
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "features": features,
                    "target": {"home_win": home_win},
                }
            )
            game_pk += 1
    return {"feature_columns": sorted(_ALL_COLUMNS), "rows": rows}


# ---------------------------------------------------------------------------
# Assembly correctness
# ---------------------------------------------------------------------------


def test_slice_by_partitions_predictions_by_label_and_computes_correct_metrics() -> None:
    predictions = [
        {"game_pk": 1, "p_home_win": 0.8, "y_true": 1},
        {"game_pk": 2, "p_home_win": 0.7, "y_true": 0},
        {"game_pk": 3, "p_home_win": 0.2, "y_true": 0},
    ]
    result = slice_by(
        predictions, lambda row: "high" if row["p_home_win"] >= 0.5 else "low"
    )

    assert result["n_excluded"] == 0
    assert set(result["buckets"]) == {"high", "low"}
    high = result["buckets"]["high"]
    assert high["n"] == 2
    assert high["avg_predicted_probability"] == pytest.approx(0.75)
    assert high["actual_win_rate"] == pytest.approx(0.5)
    assert high["calibration_gap"] == pytest.approx(0.25)
    low = result["buckets"]["low"]
    assert low["n"] == 1
    assert low["avg_predicted_probability"] == pytest.approx(0.2)
    assert low["actual_win_rate"] == pytest.approx(0.0)
    # Below LOW_N_THRESHOLD -> flagged, not omitted.
    assert low["low_confidence"] is True
    assert high["low_confidence"] is True  # n=2 also below threshold


def test_slice_by_excludes_rows_whose_label_fn_returns_none() -> None:
    predictions = [
        {"game_pk": 1, "p_home_win": 0.8, "y_true": 1},
        {"game_pk": 2, "p_home_win": 0.7, "y_true": 0},
    ]
    result = slice_by(predictions, lambda row: None if row["game_pk"] == 2 else "kept")

    assert result["n_excluded"] == 1
    assert set(result["buckets"]) == {"kept"}
    assert result["buckets"]["kept"]["n"] == 1


def test_probability_bucket_label_covers_the_full_0_to_1_range_without_gaps() -> None:
    assert _probability_bucket_label(0.0) == "0.00-0.10"
    assert _probability_bucket_label(0.05) == "0.00-0.10"
    assert _probability_bucket_label(0.55) == "0.50-0.60"
    assert _probability_bucket_label(0.999) == "0.90-1.00"
    assert _probability_bucket_label(1.0) == "0.90-1.00"


def test_edge_bucket_label_boundaries() -> None:
    assert _edge_bucket_label(-0.20) == "model_much_lower_than_market"
    assert _edge_bucket_label(-0.01) == "model_slightly_lower_than_market"
    assert _edge_bucket_label(0.01) == "model_slightly_higher_than_market"
    assert _edge_bucket_label(0.20) == "model_much_higher_than_market"


def test_tercile_labels_splits_into_three_even_groups_deterministically() -> None:
    values = {i: float(i) for i in range(9)}  # 0..8, evenly spaced
    labels = tercile_labels(values)

    assert {labels[i] for i in range(0, 3)} == {"low_tercile"}
    assert {labels[i] for i in range(3, 6)} == {"mid_tercile"}
    assert {labels[i] for i in range(6, 9)} == {"high_tercile"}


def test_tercile_labels_marks_missing_values_explicitly() -> None:
    values = {1: 1.0, 2: 2.0, 3: None, 4: 4.0}
    labels = tercile_labels(values)
    assert labels[3] == "missing"


def test_compute_failure_regimes_reports_every_required_dimension() -> None:
    result = compute_failure_regimes(_synthetic_matrix())

    required = {
        "favorite_underdog",
        "home_away_outcome",
        "probability_bucket",
        "edge_bucket",
        "starter_quality_regime",
        "bullpen_workload_regime",
        "season_fold",
    }
    assert required <= set(result["dimensions"])
    assert result["window"] == "expanding"
    # season_fold buckets exactly match the expanding-fold test seasons.
    assert set(result["dimensions"]["season_fold"]["buckets"]) == {
        "2022",
        "2023",
        "2024",
        "2025",
    }
    # favorite_underdog / home_away_outcome are always fully covered (no market
    # data required), so n_excluded must be 0 for those two dimensions.
    assert result["dimensions"]["favorite_underdog"]["n_excluded"] == 0
    assert result["dimensions"]["home_away_outcome"]["n_excluded"] == 0
    # edge_bucket has no market map supplied here -> every row excluded.
    assert result["dimensions"]["edge_bucket"]["n_excluded"] == result["n_predictions"]


def test_compute_failure_regimes_edge_bucket_uses_the_supplied_market_map() -> None:
    matrix = _synthetic_matrix(per_season=10)
    game_pks = [row["game_pk"] for row in matrix["rows"]]
    # Give every game an identical, neutral market price so every prediction's
    # edge sign is driven purely by its own p_home_win relative to 0.5.
    market_map = {pk: 0.5 for pk in game_pks}

    result = compute_failure_regimes(matrix, market_home_probability_by_game_pk=market_map)
    assert result["dimensions"]["edge_bucket"]["n_excluded"] == 0


# ---------------------------------------------------------------------------
# Leakage
# ---------------------------------------------------------------------------


def test_compute_failure_regimes_never_surfaces_holdout_season_rows() -> None:
    """``locked_model_predictions`` asserts no 2026 ``test_season``, mirroring
    ``experiments.ablation._assert_no_holdout``'s redundant check even though
    ``evaluation.splits.expanding_folds()`` already structurally excludes
    2026. This proves 2026 rows present in the input never surface in any
    slice dimension, end to end through the full ``compute_failure_regimes``
    entry point."""
    result = compute_failure_regimes(_synthetic_matrix(include_holdout=True))
    assert "2026" not in result["dimensions"]["season_fold"]["buckets"]


def test_mutating_future_season_rows_does_not_change_an_earlier_folds_slice_metrics() -> None:
    """Mirrors tests/unit/experiments/test_ablation.py's leakage-mutation pattern:
    the 2022 expanding fold trains on 2021 only and tests on 2022 only, so
    corrupting 2024/2025 feature values must not change that fold's slice."""
    matrix = _synthetic_matrix(per_season=40, seed=1)

    def _2022_bucket() -> dict:
        result = compute_failure_regimes(matrix)
        return result["dimensions"]["season_fold"]["buckets"]["2022"]

    baseline = _2022_bucket()

    mutated_rows = 0
    for row in matrix["rows"]:
        if row["game_date"].year in (2024, 2025):
            for col in row["features"]:
                row["features"][col] = 999.0
            mutated_rows += 1
    assert mutated_rows > 0

    mutated = _2022_bucket()
    assert mutated == baseline


# ---------------------------------------------------------------------------
# Regression / determinism
# ---------------------------------------------------------------------------


def test_compute_failure_regimes_is_deterministic_across_repeated_runs() -> None:
    matrix = _synthetic_matrix(seed=3)
    first = compute_failure_regimes(matrix)
    second = compute_failure_regimes(matrix)
    assert first == second


def test_low_n_threshold_matches_documented_value() -> None:
    assert LOW_N_THRESHOLD == 30
