"""Tests for the ML-013 redundancy harness (`experiments.redundancy`).

Same three required categories as the failure-regime tests: assembly
correctness, leakage (2026 rejection), and regression/determinism.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from experiments.redundancy import (
    ablation_fold_concentration,
    missingness_report,
    pairwise_correlation_scan,
    per_season_coverage_stability,
)
from features.completeness import build_feature_completeness_report


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


def _matrix(rows: list[dict]) -> dict:
    columns = sorted({c for row in rows for c in row["features"]})
    return {"feature_columns": columns, "rows": rows}


def _row(game_pk: int, season: int, features: dict, home_win: bool = True) -> dict:
    return {
        "game_pk": game_pk,
        "game_date": _dt(season, day=(game_pk % 27) + 1),
        "prediction_timestamp": _dt(season),
        "home_team_id": 10,
        "away_team_id": 20,
        "features": features,
        "target": {"home_win": home_win},
    }


# ---------------------------------------------------------------------------
# 1. Pairwise correlation
# ---------------------------------------------------------------------------


def test_pairwise_correlation_scan_flags_an_exact_duplicate_column() -> None:
    rng = np.random.default_rng(0)
    rows = []
    game_pk = 1
    for season in (2021, 2022, 2023):
        for _ in range(30):
            base = float(rng.normal())
            noise = float(rng.normal())
            rows.append(
                _row(
                    game_pk,
                    season,
                    {
                        "diff_team_a": base,
                        "diff_team_a_copy": base,  # exact duplicate -> corr == 1.0
                        "diff_team_noise": noise,  # independent -> should not flag
                    },
                )
            )
            game_pk += 1
    result = pairwise_correlation_scan(_matrix(rows), threshold=0.9)

    flagged_pairs = {
        frozenset((p["column_a"], p["column_b"])) for p in result["flagged_pairs"]
    }
    assert frozenset(("diff_team_a", "diff_team_a_copy")) in flagged_pairs
    assert not any("diff_team_noise" in pair for pair in flagged_pairs)
    assert ["diff_team_a", "diff_team_a_copy"] in result["near_duplicate_groups"]


def test_pairwise_correlation_scan_excludes_constant_columns() -> None:
    rows = []
    game_pk = 1
    for season in (2021, 2022):
        for _ in range(30):
            rows.append(_row(game_pk, season, {"diff_const": 1.0, "diff_varies": float(game_pk)}))
            game_pk += 1
    result = pairwise_correlation_scan(_matrix(rows))
    assert "diff_const" in result["constant_columns_excluded"]
    assert "diff_varies" not in result["constant_columns_excluded"]


def test_pairwise_correlation_scan_rejects_holdout_rows() -> None:
    rows = [_row(1, 2021, {"diff_x": 1.0}), _row(2, 2026, {"diff_x": 2.0})]
    with pytest.raises(ValueError, match="2026"):
        pairwise_correlation_scan(_matrix(rows))


# ---------------------------------------------------------------------------
# 2. Missingness
# ---------------------------------------------------------------------------


def test_missingness_report_reads_the_certified_completeness_report_verbatim() -> None:
    rows_raw = [
        {"features": {"diff_x": 1.0}},
        {"features": {"diff_x": None}},
        {"features": {"diff_x": 3.0}},
    ]
    completeness = build_feature_completeness_report(
        ["diff_x"], rows_raw, mode="inference"
    )
    matrix = {"feature_completeness": completeness}

    report = missingness_report(matrix)
    assert report["n_columns"] == 1
    assert report["n_columns_with_any_missing"] == 1
    entry = report["columns_with_any_missing"][0]
    assert entry["column"] == "diff_x"
    assert entry["null_rate"] == pytest.approx(1.0 / 3.0)


def test_missingness_report_requires_a_completeness_report() -> None:
    with pytest.raises(ValueError, match="feature_completeness"):
        missingness_report({"rows": []})


# ---------------------------------------------------------------------------
# 3. Per-season coverage stability
# ---------------------------------------------------------------------------


def test_per_season_coverage_stability_flags_a_season_that_drops_out() -> None:
    rows = []
    game_pk = 1
    for season in (2021, 2022, 2023, 2024):
        for i in range(20):
            # "diff_flaky" is fully populated every season except 2024, where
            # it is entirely missing -- a real coverage regression a whole-
            # build average would dilute but a per-season check must catch.
            value = None if season == 2024 else float(i)
            rows.append(_row(game_pk, season, {"diff_flaky": value, "diff_stable": 1.0 + i}))
            game_pk += 1
    result = per_season_coverage_stability(_matrix(rows), unstable_spread_threshold=0.2)

    unstable_columns = {r["column"] for r in result["unstable_columns"]}
    assert "diff_flaky" in unstable_columns
    assert "diff_stable" not in unstable_columns
    flaky = next(r for r in result["unstable_columns"] if r["column"] == "diff_flaky")
    assert flaky["by_season"]["2024"] == pytest.approx(0.0)
    assert flaky["by_season"]["2021"] == pytest.approx(1.0)


def test_per_season_coverage_stability_rejects_holdout_rows() -> None:
    rows = [_row(1, 2021, {"diff_x": 1.0}), _row(2, 2026, {"diff_x": 2.0})]
    with pytest.raises(ValueError, match="2026"):
        per_season_coverage_stability(_matrix(rows))


# ---------------------------------------------------------------------------
# 4. Ablation fold concentration (re-reads ML-012's own fold_metrics)
# ---------------------------------------------------------------------------


def _ml012_row(*, window: str, variant: str, test_season: int, log_loss: float, n_test: int = 100) -> dict:
    return {
        "window": window,
        "variant": variant,
        "test_season": test_season,
        "log_loss": log_loss,
        "n_test": n_test,
    }


def test_ablation_fold_concentration_flags_a_benefit_concentrated_in_one_fold() -> None:
    fold_metrics = [
        _ml012_row(window="expanding", variant="leave_one_out:full", test_season=2022, log_loss=0.680),
        _ml012_row(window="expanding", variant="leave_one_out:team", test_season=2022, log_loss=0.681),
        _ml012_row(window="expanding", variant="leave_one_out:full", test_season=2023, log_loss=0.680),
        _ml012_row(window="expanding", variant="leave_one_out:team", test_season=2023, log_loss=0.780),
    ]
    result = ablation_fold_concentration(
        {"fold_metrics": fold_metrics}, families=("team",), windows=("expanding",)
    )
    team = result["families"]["team"]["expanding"]
    deltas = {d["test_season"]: d["log_loss_delta"] for d in team["per_fold_deltas"]}
    assert deltas[2022] == pytest.approx(0.001)
    assert deltas[2023] == pytest.approx(0.100)
    assert team["concentrated_in_one_fold"] is True
    assert team["max_fold_share_of_total_abs_delta"] == pytest.approx(0.100 / 0.101)


def test_ablation_fold_concentration_reports_spread_when_deltas_are_even() -> None:
    fold_metrics = [
        _ml012_row(window="expanding", variant="leave_one_out:full", test_season=2022, log_loss=0.680),
        _ml012_row(window="expanding", variant="leave_one_out:team", test_season=2022, log_loss=0.700),
        _ml012_row(window="expanding", variant="leave_one_out:full", test_season=2023, log_loss=0.680),
        _ml012_row(window="expanding", variant="leave_one_out:team", test_season=2023, log_loss=0.700),
    ]
    result = ablation_fold_concentration(
        {"fold_metrics": fold_metrics}, families=("team",), windows=("expanding",)
    )
    team = result["families"]["team"]["expanding"]
    assert team["concentrated_in_one_fold"] is False
    assert team["max_fold_share_of_total_abs_delta"] == pytest.approx(0.5)


def test_ablation_fold_concentration_skips_a_family_window_with_no_matching_rows() -> None:
    result = ablation_fold_concentration(
        {"fold_metrics": []}, families=("team",), windows=("expanding",)
    )
    assert result["families"]["team"] == {}


# ---------------------------------------------------------------------------
# Regression / determinism
# ---------------------------------------------------------------------------


def test_pairwise_correlation_scan_is_deterministic() -> None:
    rng = np.random.default_rng(7)
    rows = []
    game_pk = 1
    for season in (2021, 2022):
        for _ in range(25):
            rows.append(
                _row(game_pk, season, {"diff_a": float(rng.normal()), "diff_b": float(rng.normal())})
            )
            game_pk += 1
    matrix = _matrix(rows)
    first = pairwise_correlation_scan(matrix)
    second = pairwise_correlation_scan(matrix)
    assert first == second


def test_ablation_fold_concentration_is_deterministic() -> None:
    fold_metrics = [
        _ml012_row(window="expanding", variant="leave_one_out:full", test_season=2022, log_loss=0.680),
        _ml012_row(window="expanding", variant="leave_one_out:team", test_season=2022, log_loss=0.700),
    ]
    report = {"fold_metrics": fold_metrics}
    first = ablation_fold_concentration(report, families=("team",), windows=("expanding",))
    second = ablation_fold_concentration(report, families=("team",), windows=("expanding",))
    assert first == second
