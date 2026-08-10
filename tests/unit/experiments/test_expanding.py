"""Tests for the expanding-window experiment (ML-005).

Verifies that :func:`experiments.expanding.run_expanding` drives all three model
families over the four expanding folds and emits the shared result schema:
12 fold-metric rows (4 folds x 3 models) plus a game_pk-keyed prediction table,
with valid metric ranges, no 2026 leakage, and full determinism.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from experiments.expanding import run_expanding

_REQUIRED_METRIC_KEYS = {
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
_REQUIRED_PRED_KEYS = {
    "model",
    "window",
    "test_season",
    "game_pk",
    "p_home_win",
    "y_true",
}


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, day, 19, 0, tzinfo=timezone.utc)


def _synthetic_matrix(per_season: int = 30, seed: int = 0) -> dict:
    """Learnable 2021-2025 matrix with KNOWN, unique game_pks."""
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


def test_produces_twelve_expanding_fold_metric_rows() -> None:
    result = run_expanding(_synthetic_matrix())
    fold_metrics = result["fold_metrics"]

    # 4 expanding folds x 3 model families.
    assert len(fold_metrics) == 12
    assert all(row["window"] == "expanding" for row in fold_metrics)

    # Three distinct families, each with the four expanding test seasons.
    models = {row["model"] for row in fold_metrics}
    assert len(models) == 3
    for model in models:
        seasons = sorted(
            row["test_season"] for row in fold_metrics if row["model"] == model
        )
        assert seasons == [2022, 2023, 2024, 2025]


def test_every_metric_row_has_required_keys_and_valid_ranges() -> None:
    result = run_expanding(_synthetic_matrix())

    for row in result["fold_metrics"]:
        assert set(row.keys()) == _REQUIRED_METRIC_KEYS
        assert isinstance(row["train_seasons"], list)
        assert all(isinstance(s, int) for s in row["train_seasons"])
        assert row["log_loss"] >= 0.0
        assert 0.0 <= row["brier"] <= 1.0
        assert 0.0 <= row["ece"] <= 1.0
        assert 0.0 <= row["accuracy"] <= 1.0
        assert row["roc_auc"] is None or 0.0 <= row["roc_auc"] <= 1.0
        assert row["n_train"] > 0
        assert row["n_test"] > 0
        # Expanding: train seasons strictly precede the test season.
        assert all(s < row["test_season"] for s in row["train_seasons"])


def test_prediction_rows_carry_required_keys_and_valid_probabilities() -> None:
    result = run_expanding(_synthetic_matrix())
    predictions = result["predictions"]

    assert predictions  # non-empty
    for entry in predictions:
        assert set(entry.keys()) == _REQUIRED_PRED_KEYS
        assert entry["window"] == "expanding"
        assert 0.0 <= entry["p_home_win"] <= 1.0
        assert entry["y_true"] in (0, 1)
        assert entry["test_season"] in (2022, 2023, 2024, 2025)


def test_no_holdout_season_2026_anywhere() -> None:
    result = run_expanding(_synthetic_matrix())

    assert all(row["test_season"] != 2026 for row in result["fold_metrics"])
    assert all(
        2026 not in row["train_seasons"] for row in result["fold_metrics"]
    )
    assert all(entry["test_season"] != 2026 for entry in result["predictions"])


def test_deterministic_across_two_runs() -> None:
    matrix = _synthetic_matrix()
    a = run_expanding(matrix, random_state=0)
    b = run_expanding(matrix, random_state=0)

    assert a["fold_metrics"] == b["fold_metrics"]
    assert a["predictions"] == b["predictions"]


def test_model_modules_override_selects_families() -> None:
    from models import logistic

    result = run_expanding(_synthetic_matrix(), model_modules=[logistic])
    models = {row["model"] for row in result["fold_metrics"]}

    assert models == {"logistic_regression"}
    assert len(result["fold_metrics"]) == 4  # one family x four folds
