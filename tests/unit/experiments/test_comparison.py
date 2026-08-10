"""Tests for the model x window comparison (ML-007).

These use small, precomputed ``run_expanding`` / ``run_rolling`` result dicts
(passed directly to :func:`experiments.comparison.compare_windows`) so the suite
is fast and deterministic without fitting real estimators. The synthetic data
spans the 2021-2025 development seasons with known, unique ``game_pk`` values:

- expanding is scored on test seasons {2022, 2023, 2024, 2025},
- rolling (``rolling_2`` / ``rolling_3``) is scored on {2024, 2025},

so the common (intersection) test seasons are exactly {2024, 2025}.
"""

from __future__ import annotations

import numpy as np
import pytest

from evaluation.runner import _probability_metrics
from experiments.comparison import compare_windows

_MODELS = ("logistic_regression", "xgboost")
_COMBO_KEYS = {
    "model",
    "window",
    "log_loss",
    "brier",
    "ece",
    "roc_auc",
    "accuracy",
    "n_test",
    "test_seasons",
}


def _pred(model: str, window: str, season: int, game_pk: int, p: float, y: int) -> dict:
    return {
        "model": model,
        "window": window,
        "test_season": season,
        "game_pk": game_pk,
        "p_home_win": float(p),
        "y_true": int(y),
    }


def _fold_row(model: str, window: str, season: int) -> dict:
    return {
        "model": model,
        "window": window,
        "train_seasons": [season - 1],
        "test_season": season,
        "log_loss": 0.0,
        "brier": 0.0,
        "ece": 0.0,
        "roc_auc": 0.5,
        "accuracy": 0.5,
        "n_train": 8,
        "n_test": 8,
    }


def _make_result(windows_seasons: dict[str, list[int]], *, seed: int = 0) -> dict:
    """Build a {fold_metrics, predictions} dict for the given window->seasons map."""
    rng = np.random.default_rng(seed)
    fold_metrics: list[dict] = []
    predictions: list[dict] = []
    game_pk = 100000
    for window, seasons in windows_seasons.items():
        for model in _MODELS:
            for season in seasons:
                fold_metrics.append(_fold_row(model, window, season))
                for _ in range(8):
                    p = float(rng.uniform(0.05, 0.95))
                    y = int(rng.random() < p)
                    predictions.append(_pred(model, window, season, game_pk, p, y))
                    game_pk += 1
    return {"fold_metrics": fold_metrics, "predictions": predictions}


def _expanding() -> dict:
    return _make_result({"expanding": [2022, 2023, 2024, 2025]}, seed=1)


def _rolling() -> dict:
    return _make_result({"rolling_2": [2024, 2025], "rolling_3": [2024, 2025]}, seed=2)


def test_ranking_covers_all_model_window_combos() -> None:
    result = compare_windows(expanding=_expanding(), rolling=_rolling())

    combos = {(c["model"], c["window"]) for c in result["ranking"]}
    # 2 models x 3 windows (expanding, rolling_2, rolling_3).
    assert combos == {
        (m, w)
        for m in _MODELS
        for w in ("expanding", "rolling_2", "rolling_3")
    }
    assert len(result["ranking"]) == 6
    for combo in result["ranking"]:
        assert set(combo.keys()) == _COMBO_KEYS


def test_ranking_sorted_by_primary_precedence_with_stable_tiebreak() -> None:
    result = compare_windows(expanding=_expanding(), rolling=_rolling())
    ranking = result["ranking"]

    keys = [
        (c["log_loss"], c["brier"], c["ece"], c["model"], c["window"])
        for c in ranking
    ]
    assert keys == sorted(keys)
    # 'best' is the minimum (first) under the (log_loss, brier, ece) precedence.
    assert result["best"] == ranking[0]
    assert keys[0] == min(keys)


def test_selection_uses_common_test_seasons_only() -> None:
    result = compare_windows(expanding=_expanding(), rolling=_rolling())

    assert result["common_test_seasons"] == [2024, 2025]
    # Every combo in the selection ranking is scored on the identical seasons.
    for combo in result["ranking"]:
        assert combo["test_seasons"] == [2024, 2025]


def test_full_view_uses_each_combos_own_folds() -> None:
    result = compare_windows(expanding=_expanding(), rolling=_rolling())

    full = {(c["model"], c["window"]): c for c in result["full"]}
    for model in _MODELS:
        assert full[(model, "expanding")]["test_seasons"] == [2022, 2023, 2024, 2025]
        assert full[(model, "rolling_2")]["test_seasons"] == [2024, 2025]
        assert full[(model, "rolling_3")]["test_seasons"] == [2024, 2025]


def test_pooled_metrics_match_direct_recompute_on_common_seasons() -> None:
    expanding, rolling = _expanding(), _rolling()
    result = compare_windows(expanding=expanding, rolling=rolling)
    common = set(result["common_test_seasons"])

    all_preds = expanding["predictions"] + rolling["predictions"]
    for combo in result["ranking"]:
        kept = [
            r
            for r in all_preds
            if r["model"] == combo["model"]
            and r["window"] == combo["window"]
            and r["test_season"] in common
        ]
        y = np.array([r["y_true"] for r in kept], dtype=int)
        p = np.array([r["p_home_win"] for r in kept], dtype=float)
        expected = _probability_metrics(y, p)

        assert combo["log_loss"] == expected["log_loss"]
        assert combo["brier"] == expected["brier"]
        assert combo["ece"] == expected["ece"]
        assert combo["roc_auc"] == expected["secondary"]["roc_auc"]
        assert combo["accuracy"] == expected["secondary"]["accuracy"]
        assert combo["n_test"] == expected["n_test"]


def test_no_2026_anywhere_and_selection_never_sees_holdout() -> None:
    result = compare_windows(expanding=_expanding(), rolling=_rolling())

    assert 2026 not in result["common_test_seasons"]
    for combo in result["ranking"] + result["full"]:
        assert 2026 not in combo["test_seasons"]


def test_injected_2026_row_is_rejected() -> None:
    rolling = _rolling()
    # Inject a forbidden 2026 holdout prediction row.
    rolling["predictions"].append(
        _pred("xgboost", "rolling_2", 2026, 999999, 0.6, 1)
    )
    with pytest.raises(ValueError, match="2026"):
        compare_windows(expanding=_expanding(), rolling=rolling)


def test_injected_2026_fold_metric_is_rejected() -> None:
    expanding = _expanding()
    expanding["fold_metrics"].append(_fold_row("logistic_regression", "expanding", 2026))
    with pytest.raises(ValueError, match="2026"):
        compare_windows(expanding=expanding, rolling=_rolling())


def test_deterministic_across_two_calls() -> None:
    expanding, rolling = _expanding(), _rolling()
    a = compare_windows(expanding=expanding, rolling=rolling)
    b = compare_windows(expanding=expanding, rolling=rolling)
    assert a == b


def test_default_path_runs_experiments_when_dicts_not_supplied(monkeypatch) -> None:
    """Without precomputed dicts, compare_windows calls run_expanding/run_rolling."""
    calls: dict[str, int] = {"expanding": 0, "rolling": 0}
    expanding, rolling = _expanding(), _rolling()

    def fake_expanding(matrix, *, random_state=0):
        calls["expanding"] = random_state
        return expanding

    def fake_rolling(matrix, *, random_state=0):
        calls["rolling"] = random_state
        return rolling

    monkeypatch.setattr("experiments.comparison.run_expanding", fake_expanding)
    monkeypatch.setattr("experiments.comparison.run_rolling", fake_rolling)

    result = compare_windows(matrix={"rows": []}, random_state=7)
    assert calls == {"expanding": 7, "rolling": 7}
    assert result["common_test_seasons"] == [2024, 2025]
    assert len(result["ranking"]) == 6
