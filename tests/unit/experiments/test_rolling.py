"""Unit tests for the rolling-window experiment driver (ML-006).

Verifies that :func:`experiments.rolling.run_rolling` drives all three model
families across the rolling 2- and 3-season histories, emits the shared ML-005
result schema (so ML-007 can concatenate), keeps metrics in valid ranges, never
inspects the 2026 holdout, and is deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from experiments.rolling import run_rolling
from models import logistic, random_forest, xgboost_model

# The three V1 model family names, as reported by the runner.
_MODEL_NAMES = {logistic.MODEL_NAME, random_forest.MODEL_NAME, xgboost_model.MODEL_NAME}

# Exact required keys for each schema row (no more, no less).
_FOLD_KEYS = {
    "model",
    "window",
    "train_seasons",
    "test_season",
    "log_loss",
    "brier",
    "ece",
    "roc_auc",
    "accuracy",
    "n_train",
    "n_test",
}
_PRED_KEYS = {"model", "window", "test_season", "game_pk", "p_home_win", "y_true"}

# Rolling folds over 2021-2025 development seasons test only {2024, 2025}
# (seasons with a full 3-season prior history). Two test seasons per history.
_EXPECTED_TEST_SEASONS = {2024, 2025}


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


def _synthetic_matrix(per_season: int = 40, seed: int = 0) -> dict:
    """Learnable 2021-2025 matrix with KNOWN, unique game_pks per row."""
    rng = np.random.default_rng(seed)
    rows = []
    game_pk = 1000
    for season in (2021, 2022, 2023, 2024, 2025):
        for _ in range(per_season):
            home_strength = float(rng.normal(0.0, 1.0))
            away_strength = float(rng.normal(0.0, 1.0))
            edge = home_strength - away_strength
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


def test_fold_metrics_cover_both_windows_and_all_three_models() -> None:
    result = run_rolling(_synthetic_matrix())
    fold_metrics = result["fold_metrics"]

    windows = {row["window"] for row in fold_metrics}
    assert windows == {"rolling_2", "rolling_3"}

    for window in ("rolling_2", "rolling_3"):
        models = {row["model"] for row in fold_metrics if row["window"] == window}
        assert models == _MODEL_NAMES

    # 2 histories x 3 models x 2 test seasons (2024, 2025) = 12 fold rows.
    assert len(fold_metrics) == 2 * 3 * len(_EXPECTED_TEST_SEASONS)


def test_train_seasons_length_matches_history() -> None:
    result = run_rolling(_synthetic_matrix())
    for row in result["fold_metrics"]:
        expected_len = 2 if row["window"] == "rolling_2" else 3
        assert len(row["train_seasons"]) == expected_len
        # Training seasons strictly precede the test season (chronology).
        assert all(s < row["test_season"] for s in row["train_seasons"])
        # Contiguous, ascending recent window immediately before the test season.
        assert row["train_seasons"] == list(
            range(row["test_season"] - expected_len, row["test_season"])
        )


def test_fold_metric_rows_have_exact_keys_and_valid_ranges() -> None:
    result = run_rolling(_synthetic_matrix())
    for row in result["fold_metrics"]:
        assert set(row.keys()) == _FOLD_KEYS
        assert row["log_loss"] >= 0.0
        assert 0.0 <= row["brier"] <= 1.0
        assert 0.0 <= row["ece"] <= 1.0
        assert 0.0 <= row["accuracy"] <= 1.0
        assert row["roc_auc"] is None or 0.0 <= row["roc_auc"] <= 1.0
        assert row["n_train"] > 0
        assert row["n_test"] > 0
        assert row["test_season"] in _EXPECTED_TEST_SEASONS


def test_prediction_rows_have_exact_keys_and_valid_payload() -> None:
    matrix = _synthetic_matrix()
    known_pks = {row["game_pk"] for row in matrix["rows"]}
    result = run_rolling(matrix)

    assert result["predictions"], "expected at least one prediction row"
    for row in result["predictions"]:
        assert set(row.keys()) == _PRED_KEYS
        assert row["model"] in _MODEL_NAMES
        assert row["window"] in {"rolling_2", "rolling_3"}
        assert row["test_season"] in _EXPECTED_TEST_SEASONS
        assert row["game_pk"] in known_pks
        assert 0.0 <= row["p_home_win"] <= 1.0
        assert row["y_true"] in (0, 1)


def test_holdout_2026_is_never_inspected() -> None:
    result = run_rolling(_synthetic_matrix())
    assert all(row["test_season"] != 2026 for row in result["fold_metrics"])
    assert all(2026 not in row["train_seasons"] for row in result["fold_metrics"])
    assert all(row["test_season"] != 2026 for row in result["predictions"])


def test_custom_histories_and_model_modules_are_honored() -> None:
    result = run_rolling(
        _synthetic_matrix(),
        histories=(2,),
        model_modules=(logistic,),
    )
    assert {row["window"] for row in result["fold_metrics"]} == {"rolling_2"}
    assert {row["model"] for row in result["fold_metrics"]} == {logistic.MODEL_NAME}
    # 1 history x 1 model x 2 test seasons.
    assert len(result["fold_metrics"]) == len(_EXPECTED_TEST_SEASONS)


def test_run_rolling_is_deterministic() -> None:
    matrix = _synthetic_matrix()
    a = run_rolling(matrix)
    b = run_rolling(matrix)
    assert a == b
