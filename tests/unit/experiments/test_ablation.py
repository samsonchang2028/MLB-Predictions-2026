"""Tests for the ML-012 feature-family ablation harness.

Covers the three ML-012 required-test categories on a small synthetic matrix
(same style as ``test_expanding.py`` / ``test_calibration_leakage.py``):

- assembly correctness: family/variant column membership is exact, not just
  "runs without error",
- leakage: mutating 2026-holdout / future-fold data does not change an
  earlier fold's ablation metrics,
- regression: identical input produces byte-identical output across repeated
  runs (no random-seed drift).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from evaluation.splits import expanding_folds, fold_indices, rolling_folds
from experiments.ablation import (
    FAMILIES,
    family_columns,
    incremental_variants,
    leave_one_out_variants,
    run_ablation,
)


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


_ALL_COLUMNS = (
    "home_team_win_pct_L7",
    "away_team_win_pct_L7",
    "diff_team_win_pct_L7",
    "home_starter_season_era_before",
    "away_starter_season_era_before",
    "diff_starter_season_era_before",
    "home_bullpen_bullpen_era_L7",
    "away_bullpen_bullpen_era_L7",
    "diff_bullpen_bullpen_era_L7",
    "home_starter_days_rest",
    "away_starter_days_rest",
    "diff_starter_days_rest",
)


def _synthetic_matrix(
    per_season: int = 40, seed: int = 0, include_holdout: bool = False
) -> dict:
    """A learnable dev matrix (2021-2025, optionally +2026) with real column names.

    Only ``home_team_win_pct_L7`` actually carries signal; every other column
    is pure noise, so the ablation results are used only to prove wiring
    (fold shape, no-holdout, determinism), not to draw real feature-value
    conclusions (that requires the real certified build, run separately).
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


def test_family_columns_assigns_every_column_to_the_right_family() -> None:
    families = family_columns(_ALL_COLUMNS)

    assert "unclassified" not in families
    assert set(families["team"]) == {
        "home_team_win_pct_L7",
        "away_team_win_pct_L7",
        "diff_team_win_pct_L7",
    }
    assert set(families["starter"]) == {
        "home_starter_season_era_before",
        "away_starter_season_era_before",
        "diff_starter_season_era_before",
    }
    assert set(families["bullpen"]) == {
        "home_bullpen_bullpen_era_L7",
        "away_bullpen_bullpen_era_L7",
        "diff_bullpen_bullpen_era_L7",
    }
    assert set(families["rest_schedule"]) == {
        "home_starter_days_rest",
        "away_starter_days_rest",
        "diff_starter_days_rest",
    }


def test_family_columns_reports_unclassified_columns_loudly() -> None:
    families = family_columns([*_ALL_COLUMNS, "home_weather_temperature"])
    assert families["unclassified"] == ["home_weather_temperature"]


def test_run_ablation_rejects_unclassified_columns() -> None:
    matrix = _synthetic_matrix()
    matrix["rows"][0]["features"]["home_weather_temperature"] = 1.0
    matrix["feature_columns"] = sorted([*_ALL_COLUMNS, "home_weather_temperature"])
    with pytest.raises(ValueError, match="outside the known"):
        run_ablation(matrix, windows=("expanding",))


def test_leave_one_out_variants_remove_exactly_one_family_each() -> None:
    families = family_columns(_ALL_COLUMNS)
    matrix = _synthetic_matrix()
    variants = leave_one_out_variants(matrix, families)

    assert set(variants) == {"full", *FAMILIES}
    full_columns = {
        col
        for row in variants["full"]["rows"]
        for col in row["features"]
    }
    assert full_columns == set(_ALL_COLUMNS)

    for family in FAMILIES:
        variant_columns = {
            col for row in variants[family]["rows"] for col in row["features"]
        }
        assert variant_columns == full_columns - set(families[family])
        # Exactly that one family's columns are gone -- nothing else changed.
        assert set(families[family]) - variant_columns == set(families[family])


def test_incremental_variants_are_strictly_cumulative() -> None:
    families = family_columns(_ALL_COLUMNS)
    matrix = _synthetic_matrix()
    variants = incremental_variants(matrix, families)

    assert list(variants) == [
        "team",
        "team+starter",
        "team+starter+bullpen",
        "team+starter+bullpen+rest_schedule",
    ]

    def cols(label: str) -> set[str]:
        return {c for row in variants[label]["rows"] for c in row["features"]}

    assert cols("team") == set(families["team"])
    assert cols("team+starter") == set(families["team"]) | set(families["starter"])
    assert cols("team+starter+bullpen") == (
        set(families["team"]) | set(families["starter"]) | set(families["bullpen"])
    )
    assert cols("team+starter+bullpen+rest_schedule") == set(_ALL_COLUMNS)


# ---------------------------------------------------------------------------
# Leakage
# ---------------------------------------------------------------------------


def test_no_holdout_season_anywhere_in_ablation_output() -> None:
    result = run_ablation(_synthetic_matrix(), windows=("expanding",))

    assert all(row["test_season"] != 2026 for row in result["fold_metrics"])
    assert all(2026 not in row["train_seasons"] for row in result["fold_metrics"])
    assert all(
        row["test_season"] != 2026 for row in result["sanity_check_fold_metrics"]
    )
    assert result["leakage_scan"]["n_rows_scanned"] > 0


def test_leakage_scan_rejects_input_containing_2026_rows() -> None:
    from experiments.ablation import _univariate_leakage_scan

    matrix = _synthetic_matrix(include_holdout=True)
    with pytest.raises(ValueError, match="2026"):
        _univariate_leakage_scan(matrix)


def test_mutating_future_fold_rows_does_not_change_earlier_fold_metrics() -> None:
    """Mirrors tests/leakage/test_calibration_leakage.py's mutation pattern.

    An earlier fold's train/test rows only ever come from seasons at or before
    that fold's own test season (walk-forward). Corrupting a LATER season's
    feature values must not change an earlier fold's metrics, because the
    earlier fold's estimator never sees those rows.
    """
    matrix = _synthetic_matrix(per_season=40, seed=1)
    early_fold = expanding_folds()[0]  # train=(2021,), test=2022

    def _run() -> dict:
        return run_ablation(matrix, windows=("expanding",))

    baseline = _run()
    baseline_2022 = [
        row
        for row in baseline["fold_metrics"]
        if row["test_season"] == early_fold.test_season
        and row["variant"] == "leave_one_out:full"
    ]
    assert baseline_2022

    # Corrupt every row from 2024/2025 (strictly after the early fold's train
    # AND test seasons) with an extreme, obviously-different feature value.
    mutated_rows = 0
    for row in matrix["rows"]:
        if row["game_date"].year in (2024, 2025):
            for col in row["features"]:
                row["features"][col] = 999.0
            mutated_rows += 1
    assert mutated_rows > 0

    mutated = _run()
    mutated_2022 = [
        row
        for row in mutated["fold_metrics"]
        if row["test_season"] == early_fold.test_season
        and row["variant"] == "leave_one_out:full"
    ]
    assert mutated_2022 == baseline_2022


def test_holdout_rows_never_selected_by_any_ablation_fold() -> None:
    matrix = _synthetic_matrix(include_holdout=True)
    seasons = [row["game_date"].year for row in matrix["rows"]]
    holdout_positions = {i for i, s in enumerate(seasons) if s == 2026}
    assert holdout_positions

    for scheme in (expanding_folds(), rolling_folds(2), rolling_folds(3)):
        for fold in scheme:
            train_idx, test_idx = fold_indices(fold, seasons)
            selected = set(train_idx) | set(test_idx)
            assert holdout_positions.isdisjoint(selected)


# ---------------------------------------------------------------------------
# Regression / determinism
# ---------------------------------------------------------------------------


def test_run_ablation_is_deterministic_across_repeated_runs() -> None:
    matrix = _synthetic_matrix()
    first = run_ablation(matrix, windows=("expanding",))
    second = run_ablation(matrix, windows=("expanding",))

    assert first["fold_metrics"] == second["fold_metrics"]
    assert first["variant_aggregates"] == second["variant_aggregates"]
    assert first["sanity_check_fold_metrics"] == second["sanity_check_fold_metrics"]
    assert (
        first["sanity_check_variant_aggregates"]
        == second["sanity_check_variant_aggregates"]
    )
    assert first["leakage_scan"] == second["leakage_scan"]
    assert first["families"] == second["families"]


def test_run_ablation_produces_expected_fold_row_counts() -> None:
    result = run_ablation(_synthetic_matrix(), windows=("expanding",))

    n_variants = 1 + len(FAMILIES) + len(FAMILIES)  # full + 4 leave-one-out + 4 incremental
    n_expanding_folds = len(expanding_folds())
    assert len(result["fold_metrics"]) == n_variants * n_expanding_folds
    assert len(result["variant_aggregates"]) == n_variants  # one window x pooled

    # Sanity checks: 2 models x 5 leave-one-out variants x expanding folds.
    assert len(result["sanity_check_fold_metrics"]) == (
        2 * (1 + len(FAMILIES)) * n_expanding_folds
    )
    assert len(result["sanity_check_variant_aggregates"]) == 2 * (1 + len(FAMILIES))


def test_variant_aggregate_pooled_log_loss_is_the_n_weighted_fold_mean() -> None:
    """log loss/Brier ARE simple per-sample means, so pooling across folds must
    equal the n_test-weighted average of the per-fold values (unlike ECE, which
    is a nonlinear binned statistic and is intentionally NOT re-derived this
    way anywhere in this harness)."""
    result = run_ablation(_synthetic_matrix(), windows=("expanding",))
    fold_rows = [
        row
        for row in result["fold_metrics"]
        if row["variant"] == "leave_one_out:full"
    ]
    aggregate_row = next(
        row
        for row in result["variant_aggregates"]
        if row["variant"] == "leave_one_out:full"
    )

    n = sum(r["n_test"] for r in fold_rows)
    expected_log_loss = sum(r["log_loss"] * r["n_test"] for r in fold_rows) / n
    assert aggregate_row["log_loss"] == pytest.approx(expected_log_loss, abs=1e-9)
    assert aggregate_row["n_test"] == n
