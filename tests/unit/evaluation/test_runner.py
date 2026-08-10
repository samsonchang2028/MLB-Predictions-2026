"""Unit tests for the walk-forward runner (ML-004).

Covers deterministic vectorization (stable sorted schema, NaN fill, target
isolation), uniform driving of all three model families, primary/secondary
metric ranges, and graceful handling of a degenerate single-class test fold.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from evaluation.runner import run_evaluation, vectorize_matrix
from evaluation.splits import Fold, expanding_folds
from models import logistic, random_forest, xgboost_model

ALL_FAMILIES = [logistic, random_forest, xgboost_model]


def _dt(year: int, month: int = 4, day: int = 1) -> datetime:
    return datetime(year, month, day, 19, 0, tzinfo=timezone.utc)


def _synthetic_matrix(
    per_season: int = 40, seed: int = 0
) -> dict:
    """Learnable 2021-2025 matrix with a signal linking features to home_win."""
    rng = np.random.default_rng(seed)
    rows = []
    game_pk = 1000
    for season in (2021, 2022, 2023, 2024, 2025):
        for _ in range(per_season):
            home_strength = float(rng.normal(0.0, 1.0))
            away_strength = float(rng.normal(0.0, 1.0))
            edge = home_strength - away_strength
            # Home wins more often when its strength edge is positive.
            prob = 1.0 / (1.0 + np.exp(-edge))
            home_win = bool(rng.random() < prob)
            rows.append(
                {
                    "game_pk": game_pk,
                    "game_date": _dt(season, day=(game_pk % 27) + 1),
                    "prediction_timestamp": _dt(season),
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "features": {
                        "home_team_strength": home_strength,
                        "away_team_strength": away_strength,
                        "diff_team_strength": edge,
                    },
                    "target": {"home_win": home_win},
                }
            )
            game_pk += 1
    return {"feature_columns": [], "rows": rows}


def test_vectorize_stable_sorted_schema_with_nan_fill() -> None:
    rows = [
        {
            "game_pk": 2,
            "game_date": _dt(2022),
            "features": {"b_feat": 1.0, "a_feat": 2.0},
            "target": {"home_win": True},
        },
        {
            "game_pk": 1,
            "game_date": _dt(2021),
            # missing "b_feat" -> must be NaN, schema still the sorted union.
            "features": {"a_feat": 3.0, "c_feat": 5.0},
            "target": {"home_win": False},
        },
    ]
    X, y, seasons, columns = vectorize_matrix(rows)

    # Sorted union of all feature keys across rows.
    assert columns == ["a_feat", "b_feat", "c_feat"]
    # Chronological ordering by (game_date, game_pk): 2021 row first.
    assert seasons == [2021, 2022]
    # Row 0 (2021) is missing b_feat -> NaN; row 1 (2022) is missing c_feat -> NaN.
    assert np.isnan(X[0, columns.index("b_feat")])
    assert np.isnan(X[1, columns.index("c_feat")])
    # Target read only from the isolated slot.
    assert list(y) == [0.0, 1.0]


def test_vectorize_ignores_non_numeric_and_undecided_target() -> None:
    rows = [
        {
            "game_pk": 1,
            "game_date": _dt(2021),
            "features": {"num": 1.0, "text": "abc", "flag": True},
            "target": {"home_win": None},  # undecided
        },
    ]
    X, y, _seasons, columns = vectorize_matrix(rows)
    assert columns == ["flag", "num", "text"]
    assert X[0, columns.index("num")] == 1.0
    assert X[0, columns.index("flag")] == 1.0  # bool -> 1.0
    assert np.isnan(X[0, columns.index("text")])  # non-numeric -> NaN
    assert np.isnan(y[0])  # undecided label -> NaN


@pytest.mark.parametrize("family", ALL_FAMILIES, ids=lambda m: m.MODEL_NAME)
def test_all_families_evaluate_with_valid_metric_ranges(family) -> None:
    matrix = _synthetic_matrix()
    report = run_evaluation(family, matrix, expanding_folds(), random_state=0)

    assert report["model"] == family.MODEL_NAME
    assert report["feature_columns"] == [
        "away_team_strength",
        "diff_team_strength",
        "home_team_strength",
    ]
    assert len(report["folds"]) == 4
    assert report["aggregate"]["n_folds"] == 4

    for block in report["folds"] + [report["aggregate"]]:
        assert block["log_loss"] >= 0.0
        assert 0.0 <= block["brier"] <= 1.0
        assert 0.0 <= block["ece"] <= 1.0
        roc = block["secondary"]["roc_auc"]
        assert roc is None or 0.0 <= roc <= 1.0
        assert 0.0 <= block["secondary"]["accuracy"] <= 1.0


@pytest.mark.parametrize("family", ALL_FAMILIES, ids=lambda m: m.MODEL_NAME)
def test_results_are_deterministic(family) -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    a = run_evaluation(family, matrix, folds, random_state=0)
    b = run_evaluation(family, matrix, folds, random_state=0)

    assert [f["log_loss"] for f in a["folds"]] == [f["log_loss"] for f in b["folds"]]
    assert a["aggregate"]["log_loss"] == b["aggregate"]["log_loss"]


def test_accepts_build_model_callable_directly() -> None:
    matrix = _synthetic_matrix()
    report = run_evaluation(
        logistic.build_model,
        matrix,
        expanding_folds(),
        random_state=0,
        model_name="logistic_via_callable",
    )
    assert report["model"] == "logistic_via_callable"
    assert len(report["folds"]) == 4


def test_degenerate_single_class_test_fold_is_handled() -> None:
    """A test fold whose labels are all one class must not crash the runner."""
    rng = np.random.default_rng(1)
    rows = []
    game_pk = 0
    for season in (2021, 2022):
        for _ in range(30):
            f = float(rng.normal())
            # Train season (2021) has both classes; test season (2022) is all wins.
            home_win = bool(rng.random() < 0.5) if season == 2021 else True
            rows.append(
                {
                    "game_pk": game_pk,
                    "game_date": _dt(season),
                    "features": {"f": f},
                    "target": {"home_win": home_win},
                }
            )
            game_pk += 1
    matrix = {"rows": rows}

    report = run_evaluation(logistic, matrix, [Fold((2021,), 2022)], random_state=0)
    fold = report["folds"][0]

    assert fold["degenerate_single_class"] is True
    assert fold["secondary"]["roc_auc"] is None  # undefined for one class
    # Primary probability metrics remain finite and well-defined.
    assert np.isfinite(fold["log_loss"])
    assert 0.0 <= fold["brier"] <= 1.0
    assert 0.0 <= fold["ece"] <= 1.0
