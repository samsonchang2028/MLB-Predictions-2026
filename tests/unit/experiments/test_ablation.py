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
from features.completeness import REQUIRED_FAMILY_COLUMNS


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


# ---------------------------------------------------------------------------
# Tester (ML-012): additional adversarial coverage
# ---------------------------------------------------------------------------
#
# Everything below was added by the tester pass, on top of the implementer's
# 14 tests above. It targets six angles the tester role treats as high-risk
# for this task: (1) family assembly against the REAL 240-column production
# taxonomy, not just the 12-column fixture; (2) the FULL/zero-column boundary
# cases, not just a middle case; (3) determinism under adversarial column
# ordering; (4) that the harness reuses evaluation.splits's real fold
# boundaries rather than reinventing them; (5) a broader mutation-based
# leakage test spanning every variant and window, not just one; (6) an
# end-to-end (not just unit-level) 2026-holdout rejection test, plus one
# xfail-pinned finding.


def test_family_columns_matches_real_completeness_taxonomy_on_full_column_set() -> None:
    """Column-membership assertion against the actual production taxonomy
    (``features.completeness.REQUIRED_FAMILY_COLUMNS``), not the small
    12-column synthetic fixture -- proves ``family_columns`` partitions the
    real 240-column certified-build taxonomy with the documented exact counts
    and zero leftovers."""
    all_real_columns = [col for cols in REQUIRED_FAMILY_COLUMNS.values() for col in cols]
    assert len(all_real_columns) == 240

    families = family_columns(all_real_columns)

    assert "unclassified" not in families
    assert len(families["team"]) == 84
    assert len(families["starter"]) == 75
    assert len(families["bullpen"]) == 45
    assert len(families["rest_schedule"]) == 36
    for family, expected_columns in REQUIRED_FAMILY_COLUMNS.items():
        assert set(families[family]) == set(expected_columns)


def test_leave_one_out_full_variant_excludes_unclassified_columns_directly() -> None:
    """Boundary case for the FULL model, called directly (bypassing
    ``run_ablation``'s own unclassified-column guard): ``leave_one_out_variants``
    must restrict ``"full"`` to only the four known families' columns, not
    every column physically present in the matrix."""
    matrix = _synthetic_matrix()
    for row in matrix["rows"]:
        row["features"]["home_weather_temperature"] = 1.0
    stray_columns = [*_ALL_COLUMNS, "home_weather_temperature"]
    matrix["feature_columns"] = sorted(stray_columns)

    families = family_columns(stray_columns)
    assert families["unclassified"] == ["home_weather_temperature"]

    variants = leave_one_out_variants(matrix, families)
    full_columns = {c for row in variants["full"]["rows"] for c in row["features"]}
    assert full_columns == set(_ALL_COLUMNS)
    assert "home_weather_temperature" not in full_columns


def test_incremental_final_variant_matches_leave_one_out_full_exactly() -> None:
    """Cross-invariant documented in the module: the incremental scheme's
    final cumulative step must cover exactly the same columns as
    ``leave_one_out_variants(...)["full"]`` -- both represent the same
    'every known family present' model."""
    families = family_columns(_ALL_COLUMNS)
    matrix = _synthetic_matrix()

    loo = leave_one_out_variants(matrix, families)
    inc = incremental_variants(matrix, families)

    full_cols = {c for row in loo["full"]["rows"] for c in row["features"]}
    final_key = "+".join(FAMILIES)
    final_cols = {c for row in inc[final_key]["rows"] for c in row["features"]}
    assert final_cols == full_cols


def test_subset_matrix_handles_the_zero_column_baseline_without_crashing() -> None:
    """Baseline boundary: carving a variant down to zero feature columns (as
    if every known family were removed) must not crash or silently retain
    any column -- exercises the assembly logic at its emptiest edge, not
    just a middle case."""
    from experiments.ablation import _subset_matrix

    matrix = _synthetic_matrix(per_season=5)
    subset = _subset_matrix(matrix, set())

    assert subset["feature_columns"] == []
    assert subset["rows"]
    assert all(row["features"] == {} for row in subset["rows"])


def test_run_ablation_is_order_independent_of_feature_column_ordering() -> None:
    """Determinism under adversarial column ordering: reversing
    ``feature_columns`` and each row's ``features`` dict insertion order must
    not change the ablation result. Catches iteration-order non-determinism
    that dict-based family/variant assembly could otherwise be sensitive to
    (this harness sorts column sets and iterates the fixed ``FAMILIES``
    tuple, so it should be immune -- this test proves that, rather than
    assuming it)."""
    matrix = _synthetic_matrix(seed=3)
    baseline = run_ablation(matrix, windows=("expanding",))

    shuffled_columns = list(reversed(_ALL_COLUMNS))
    shuffled_rows = []
    for row in matrix["rows"]:
        shuffled_row = dict(row)
        shuffled_row["features"] = {col: row["features"][col] for col in shuffled_columns}
        shuffled_rows.append(shuffled_row)
    shuffled_matrix = {"feature_columns": shuffled_columns, "rows": shuffled_rows}

    shuffled_result = run_ablation(shuffled_matrix, windows=("expanding",))

    assert shuffled_result["fold_metrics"] == baseline["fold_metrics"]
    assert shuffled_result["variant_aggregates"] == baseline["variant_aggregates"]
    assert shuffled_result["families"] == baseline["families"]


def test_ablation_reuses_evaluation_splits_fold_boundaries_exactly() -> None:
    """Chronology/window integrity: the harness must resolve folds via the
    real ``evaluation.splits`` functions for ALL three window schemes, not
    silently construct its own boundaries (which could reintroduce
    train/test overlap bugs already solved elsewhere in this repo)."""
    matrix = _synthetic_matrix()
    result = run_ablation(
        matrix, windows=("expanding", "rolling_2", "rolling_3"), sanity_models=()
    )

    expected = {
        "expanding": expanding_folds(),
        "rolling_2": rolling_folds(2),
        "rolling_3": rolling_folds(3),
    }
    for window, folds in expected.items():
        expected_pairs = {(f.train_seasons, f.test_season) for f in folds}
        actual_pairs = {
            (tuple(row["train_seasons"]), row["test_season"])
            for row in result["fold_metrics"]
            if row["window"] == window and row["variant"] == "leave_one_out:full"
        }
        assert actual_pairs == expected_pairs


def test_mutating_future_fold_data_never_changes_earlier_folds_across_variants_and_windows() -> None:
    """Broader mutation-based leakage test than the implementer's single
    leave_one_out:full/expanding case: proves the invariant holds across
    every variant (leave-one-out AND incremental) and every window
    (expanding, rolling_2, rolling_3) at once, using the real fold set the
    harness actually drives."""
    matrix = _synthetic_matrix(per_season=40, seed=2)
    windows = ("expanding", "rolling_2", "rolling_3")

    def _run() -> dict:
        return run_ablation(matrix, windows=windows, sanity_models=())

    def _unaffected_rows(result: dict) -> dict:
        # Folds whose train AND test seasons never touch 2024/2025 cannot be
        # affected by mutating 2024/2025 feature values under any correct
        # walk-forward implementation.
        return {
            (row["window"], row["variant"], row["test_season"]): row
            for row in result["fold_metrics"]
            if row["test_season"] not in (2024, 2025)
            and all(s not in (2024, 2025) for s in row["train_seasons"])
        }

    baseline = _run()
    baseline_unaffected = _unaffected_rows(baseline)
    assert baseline_unaffected  # sanity: such folds actually exist (2022/2023)

    mutated_rows = 0
    for row in matrix["rows"]:
        if row["game_date"].year in (2024, 2025):
            for col in row["features"]:
                row["features"][col] = -999.0
            mutated_rows += 1
    assert mutated_rows > 0

    mutated = _run()
    mutated_unaffected = _unaffected_rows(mutated)

    assert mutated_unaffected == baseline_unaffected


def test_run_ablation_raises_end_to_end_when_2026_rows_are_present() -> None:
    """Adversarial 2026-exclusion check driven through the full
    ``run_ablation`` entry point (not just the internal
    ``_univariate_leakage_scan`` helper in isolation, which the implementer's
    existing test already covers): proves the harness as actually invoked by
    ``scripts/ml012_feature_ablation.py`` refuses contaminated input rather
    than silently completing."""
    matrix = _synthetic_matrix(include_holdout=True)
    with pytest.raises(ValueError, match="2026"):
        run_ablation(matrix, windows=("expanding",))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P2 known gap (unreachable via the real ML-012 production path: "
        "scripts/ml012_feature_ablation.py always builds its matrix through "
        "build_feature_matrix, which carries build_id/feature_completeness, "
        "and FEAT-006 already FAILs Gold completeness when a required family "
        "is entirely absent -- 'family entirely absent' is one of its "
        "blocking_issues -- so a certified-PASS build can never reach "
        "run_ablation with a genuinely empty family). But run_ablation and "
        "leave_one_out_variants have no independent guard of their own: if a "
        "family's REQUIRED_FAMILY_COLUMNS set has zero columns actually "
        "present in feature_columns (e.g. a hand-built matrix bypassing "
        "FEAT-006, or a future matrix shape that skips the completeness "
        "gate), leave_one_out_variants[<family>] silently equals "
        "leave_one_out_variants['full'] (removing an empty set is a no-op). "
        "The ablation report would then read as 'removing bullpen has zero "
        "effect on log loss' when in truth bullpen was never in the input "
        "at all -- a materially different, misleading conclusion for a "
        "research report whose whole purpose is family-level KEEP/REMOVE "
        "verdicts. Fix: family_columns/run_ablation should raise (or the "
        "family report should flag) any FAMILIES entry with zero present "
        "columns, mirroring the existing unclassified-column guard."
    ),
)
def test_leave_one_out_family_removal_is_not_a_silent_no_op_when_family_is_entirely_absent() -> None:
    columns = tuple(c for c in _ALL_COLUMNS if "_bullpen_" not in c)
    matrix = _synthetic_matrix()
    for row in matrix["rows"]:
        row["features"] = {c: v for c, v in row["features"].items() if c in columns}
    matrix["feature_columns"] = sorted(columns)

    families = family_columns(columns)
    assert families["bullpen"] == []  # sanity: the family is genuinely absent

    variants = leave_one_out_variants(matrix, families)
    full_cols = {c for row in variants["full"]["rows"] for c in row["features"]}
    bullpen_removed_cols = {c for row in variants["bullpen"]["rows"] for c in row["features"]}

    assert bullpen_removed_cols != full_cols, (
        "family 'bullpen' has zero present columns but its leave-one-out "
        "variant is byte-identical to 'full' -- the report would misreport "
        "this as 'removing bullpen has zero effect' instead of flagging "
        "that bullpen was never in the input"
    )
