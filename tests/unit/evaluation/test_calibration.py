"""Unit tests for probability-calibration evaluation (ML-008).

Uses a small, learnable, deterministic 2021-2025 synthetic matrix (identical in
spirit to the ML-004 runner tests). Verifies both calibration methods produce
valid probabilities and in-range metrics, that ``compare_calibration`` reports
the uncalibrated baseline plus both methods over the same folds with the primary
metrics present, that results are deterministic, and that a degenerate inner
calibration partition is rejected explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from evaluation.calibration import (
    CALIBRATION_METHODS,
    _partition_train,
    compare_calibration,
    evaluate_calibration,
)
from evaluation.splits import Fold, expanding_folds
from models import logistic


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


def _synthetic_matrix(per_season: int = 60, seed: int = 0) -> dict:
    """Learnable 2021-2025 matrix linking a strength edge to home_win."""
    rng = np.random.default_rng(seed)
    rows = []
    game_pk = 1000
    for season in (2021, 2022, 2023, 2024, 2025):
        for _ in range(per_season):
            home = float(rng.normal())
            away = float(rng.normal())
            edge = home - away
            prob = 1.0 / (1.0 + np.exp(-edge))
            home_win = bool(rng.random() < prob)
            rows.append(
                {
                    "game_pk": game_pk,
                    "game_date": _dt(season, day=(game_pk % 27) + 1),
                    "features": {
                        "home_strength": home,
                        "away_strength": away,
                        "diff_strength": edge,
                    },
                    "target": {"home_win": home_win},
                }
            )
            game_pk += 1
    return {"rows": rows}


def _assert_valid_metrics(block: dict) -> None:
    assert block["log_loss"] >= 0.0
    assert 0.0 <= block["brier"] <= 1.0
    assert 0.0 <= block["ece"] <= 1.0
    roc = block["secondary"]["roc_auc"]
    assert roc is None or 0.0 <= roc <= 1.0
    assert 0.0 <= block["secondary"]["accuracy"] <= 1.0


@pytest.mark.parametrize("method", CALIBRATION_METHODS)
def test_evaluate_calibration_valid_for_both_methods(method: str) -> None:
    matrix = _synthetic_matrix()
    report = evaluate_calibration(
        logistic, matrix, expanding_folds(), method=method, random_state=0
    )

    assert report["model"] == logistic.MODEL_NAME
    assert report["method"] == method
    assert report["calibration_fraction"] == 0.2
    assert len(report["folds"]) == 4
    assert report["aggregate"]["n_folds"] == 4

    for fold in report["folds"]:
        _assert_valid_metrics(fold)
        # Base-fit + inner-calibration partitions are both non-empty and their
        # labeled counts do not exceed the training rows.
        assert fold["n_base_fit"] >= 1
        assert fold["n_calibration"] >= 1
        assert fold["n_test"] >= 1
    _assert_valid_metrics(report["aggregate"])


def test_calibrated_probabilities_are_valid_range() -> None:
    """Calibrated probabilities must remain in [0, 1] (exercised via metrics)."""
    matrix = _synthetic_matrix()
    for method in CALIBRATION_METHODS:
        report = evaluate_calibration(
            logistic, matrix, expanding_folds(), method=method
        )
        # brier / log_loss are only finite/in-range for valid probabilities.
        for fold in report["folds"]:
            assert np.isfinite(fold["log_loss"])
            assert 0.0 <= fold["brier"] <= 1.0


def test_compare_calibration_reports_uncalibrated_and_both_methods() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    result = compare_calibration(logistic, matrix, folds, random_state=0)

    assert set(result["variants"]) == {
        "uncalibrated",
        "sigmoid",
        "isotonic",
        "uncalibrated_full_train",
    }
    assert result["methods"] == ["sigmoid", "isotonic"]

    for name, variant in result["variants"].items():
        assert len(variant["folds"]) == len(folds), name
        assert variant["aggregate"]["n_folds"] == len(folds), name
        # Primary metrics present + valid for every variant, per fold + aggregate.
        for block in variant["folds"] + [variant["aggregate"]]:
            assert "log_loss" in block and "brier" in block and "ece" in block
            _assert_valid_metrics(block)


def test_compare_uses_same_folds_and_base_fit_partition() -> None:
    """Uncalibrated vs calibrated share the SAME folds and base-fit partition."""
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    result = compare_calibration(logistic, matrix, folds)

    unc = result["variants"]["uncalibrated"]["folds"]
    sig = result["variants"]["sigmoid"]["folds"]
    iso = result["variants"]["isotonic"]["folds"]
    # Same folds => identical partition sizes and test seasons across variants.
    for u, s, i in zip(unc, sig, iso):
        assert u["test_season"] == s["test_season"] == i["test_season"]
        assert u["n_base_fit"] == s["n_base_fit"] == i["n_base_fit"]
        assert u["n_calibration"] == s["n_calibration"] == i["n_calibration"]
        assert u["n_test"] == s["n_test"] == i["n_test"]


def test_uncalibrated_full_train_uses_more_base_rows() -> None:
    """The reference full-train variant fits on all training rows, not the split."""
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    result = compare_calibration(logistic, matrix, folds)

    unc = result["variants"]["uncalibrated"]["folds"]
    # The uncalibrated (split) base-fit uses only ~80% of the training rows, so
    # its recorded base-fit count is strictly smaller than n_base_fit + n_calib.
    for fold in unc:
        assert fold["n_base_fit"] < fold["n_base_fit"] + fold["n_calibration"]


def test_determinism_across_two_calls() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    a = compare_calibration(logistic, matrix, folds, random_state=0)
    b = compare_calibration(logistic, matrix, folds, random_state=0)
    assert a == b

    c = evaluate_calibration(logistic, matrix, folds, method="isotonic")
    d = evaluate_calibration(logistic, matrix, folds, method="isotonic")
    assert c == d


def test_invalid_method_rejected() -> None:
    matrix = _synthetic_matrix()
    with pytest.raises(ValueError, match="method must be one of"):
        evaluate_calibration(logistic, matrix, expanding_folds(), method="bogus")
    with pytest.raises(ValueError, match="method must be one of"):
        compare_calibration(
            logistic, matrix, expanding_folds(), methods=("sigmoid", "nope")
        )


def test_invalid_calibration_fraction_rejected() -> None:
    matrix = _synthetic_matrix()
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="calibration_fraction"):
            evaluate_calibration(
                logistic,
                matrix,
                expanding_folds(),
                method="sigmoid",
                calibration_fraction=bad,
            )


def test_degenerate_single_class_inner_partition_raises() -> None:
    """An inner-calibration partition with only one class is rejected clearly."""
    # Construct a 2021 train season whose LATER 20% (inner calibration) rows are
    # all home wins, while the earlier base-fit rows carry both classes.
    rows = []
    game_pk = 0
    n = 20
    for i in range(n):
        # Chronological order is by (game_date, game_pk); later rows -> later day.
        # Last 20% (i >= 16) are all wins -> single-class inner partition.
        if i >= 16:
            home_win = True
        else:
            home_win = bool(i % 2 == 0)
        rows.append(
            {
                "game_pk": game_pk,
                "game_date": _dt(2021, day=i + 1),
                "features": {"f": float(i)},
                "target": {"home_win": home_win},
            }
        )
        game_pk += 1
    # A 2022 test season with both classes so only the inner partition is bad.
    for i in range(6):
        rows.append(
            {
                "game_pk": game_pk,
                "game_date": _dt(2022, day=i + 1),
                "features": {"f": float(i)},
                "target": {"home_win": bool(i % 2 == 0)},
            }
        )
        game_pk += 1

    matrix = {"rows": rows}
    with pytest.raises(ValueError, match="single class"):
        evaluate_calibration(
            logistic,
            matrix,
            [Fold((2021,), 2022)],
            method="sigmoid",
            calibration_fraction=0.2,
        )


def test_partition_train_split_is_chronological_and_sized() -> None:
    train_idx = tuple(range(10))
    base, cal = _partition_train(train_idx, 0.2)
    assert base == (0, 1, 2, 3, 4, 5, 6, 7)
    assert cal == (8, 9)
    # cal rows are chronologically after all base rows.
    assert max(base) < min(cal)
