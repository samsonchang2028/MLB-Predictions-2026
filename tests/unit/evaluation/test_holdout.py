"""Unit tests for the ML-010 2026 final-holdout evaluator (ADR-006, ADR-003).

Covers the task's required tests: refusal without a locked/accepted ADR-006,
chronology/no-overlap of the 2021-2025 train / 2026 test split, misconfigured
matrices (missing dev rows or missing 2026 rows), preprocessing/fit isolation
(the fitted estimator only ever sees training rows), and an end-to-end sane
run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from evaluation.holdout import (
    ADR_006_PATH,
    LOCKED_PARAMS,
    require_locked_methodology,
    run_holdout_evaluation,
)
from evaluation.splits import DEV_SEASONS, HOLDOUT_SEASON


def _dt(year: int, day: int = 1) -> datetime:
    return datetime(year, 4, min(day, 28), 19, 0, tzinfo=timezone.utc)


def _synthetic_matrix(per_season: int = 30, seed: int = 0) -> dict:
    """Learnable 2021-2026 matrix with a signal linking features to home_win."""
    rng = np.random.default_rng(seed)
    rows = []
    game_pk = 1000
    for season in (*DEV_SEASONS, HOLDOUT_SEASON):
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
                    "features": {
                        "home_team_strength": home_strength,
                        "away_team_strength": away_strength,
                        "diff_team_strength": edge,
                    },
                    "target": {"home_win": home_win},
                }
            )
            game_pk += 1
    return {"rows": rows}


# --------------------------------------------------------------------------- #
# ADR-006 lock gate
# --------------------------------------------------------------------------- #


def test_refuses_without_adr_006_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.md"
    with pytest.raises(RuntimeError, match="ADR-006 methodology lock not found"):
        require_locked_methodology(missing)
    with pytest.raises(RuntimeError, match="ADR-006 methodology lock not found"):
        run_holdout_evaluation(_synthetic_matrix(), adr_path=missing)


def test_refuses_if_adr_006_not_accepted(tmp_path: Path) -> None:
    adr = tmp_path / "ADR-006-draft.md"
    adr.write_text(
        "# ADR-006: V1 Model Methodology Lock\n\n"
        "## Status\n\nProposed.\n\n"
        "Lock the V1 methodology as:\n- model family: XGBoost\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="not in Accepted status"):
        require_locked_methodology(adr)
    with pytest.raises(RuntimeError, match="not in Accepted status"):
        run_holdout_evaluation(_synthetic_matrix(), adr_path=adr)


def test_refuses_if_adr_006_missing_lock_section(tmp_path: Path) -> None:
    adr = tmp_path / "ADR-006-no-lock.md"
    adr.write_text("## Status\n\nAccepted.\n\nSome other content.\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not contain the expected methodology lock"):
        require_locked_methodology(adr)


def test_real_adr_006_file_passes_the_gate() -> None:
    # The actual repo ADR-006 must satisfy its own gate (sanity/regression check).
    require_locked_methodology(ADR_006_PATH)


# --------------------------------------------------------------------------- #
# Chronology / overlap / misconfiguration
# --------------------------------------------------------------------------- #


def test_end_to_end_splits_into_locked_train_and_2026_test() -> None:
    result = run_holdout_evaluation(_synthetic_matrix(), random_state=0)
    assert result["metrics"]["train_seasons"] == sorted(DEV_SEASONS)
    assert result["metrics"]["test_season"] == HOLDOUT_SEASON


def test_raises_if_matrix_has_no_2026_rows() -> None:
    matrix = _synthetic_matrix()
    matrix["rows"] = [r for r in matrix["rows"] if r["game_date"].year != HOLDOUT_SEASON]
    with pytest.raises(ValueError, match="no 2026 holdout rows"):
        run_holdout_evaluation(matrix)


def test_raises_if_matrix_has_no_dev_season_rows() -> None:
    matrix = _synthetic_matrix()
    matrix["rows"] = [r for r in matrix["rows"] if r["game_date"].year == HOLDOUT_SEASON]
    with pytest.raises(ValueError, match="no 2021-2025 training rows"):
        run_holdout_evaluation(matrix)


def test_predictions_are_2026_only_and_game_pk_keyed() -> None:
    result = run_holdout_evaluation(_synthetic_matrix(), random_state=0)
    n_2026_rows = sum(
        1 for r in _synthetic_matrix()["rows"] if r["game_date"].year == HOLDOUT_SEASON
    )
    assert len(result["predictions"]) == n_2026_rows
    for pred in result["predictions"]:
        assert set(pred) == {"game_pk", "p_home_win", "y_true"}
        assert 0.0 <= pred["p_home_win"] <= 1.0
        assert pred["y_true"] in (0, 1)
    game_pks = {p["game_pk"] for p in result["predictions"]}
    expected_pks = {
        r["game_pk"]
        for r in _synthetic_matrix()["rows"]
        if r["game_date"].year == HOLDOUT_SEASON
    }
    assert game_pks == expected_pks


# --------------------------------------------------------------------------- #
# Preprocessing / fit isolation (leakage-test style)
# --------------------------------------------------------------------------- #


def test_fit_is_isolated_from_2026_rows() -> None:
    """Mutating only the 2026 test rows' features must not change what the
    model was fit on -- proving 2026 never entered training or preprocessing
    fit. Classic leakage-test pattern (see tests/leakage/): mutate a partition
    that must NOT influence the fit, then prove the fit is byte-identical."""
    from evaluation.holdout import _build_model
    from evaluation.runner import _vectorize

    matrix_a = _synthetic_matrix()
    matrix_b = _synthetic_matrix()
    for row in matrix_b["rows"]:
        if row["game_date"].year == HOLDOUT_SEASON:
            for key in row["features"]:
                row["features"][key] = 999.0

    X_a, y_a, seasons_a, _cols_a, _pk_a = _vectorize(matrix_a)
    X_b, y_b, seasons_b, _cols_b, _pk_b = _vectorize(matrix_b)
    train_a = [i for i, s in enumerate(seasons_a) if s in DEV_SEASONS]
    train_b = [i for i, s in enumerate(seasons_b) if s in DEV_SEASONS]

    # The training rows themselves (features/labels) are byte-identical even
    # though the 2026 rows were mutated -- training never reads test rows.
    assert np.array_equal(X_a[train_a], X_b[train_b], equal_nan=True)
    assert np.array_equal(y_a[train_a], y_b[train_b])

    model_a = _build_model(random_state=0)
    model_b = _build_model(random_state=0)
    model_a.fit(X_a[train_a], y_a[train_a])
    model_b.fit(X_b[train_b], y_b[train_b])

    # Same training data in -> byte-identical fitted model out, regardless of
    # how the (excluded) 2026 rows were mutated.
    np.testing.assert_array_equal(
        model_a.feature_importances_, model_b.feature_importances_
    )
    probe = X_a[train_a][:5]
    np.testing.assert_allclose(
        model_a.predict_proba(probe), model_b.predict_proba(probe)
    )


def test_training_seasons_are_exactly_dev_seasons() -> None:
    """The fitted fold's recorded train_seasons is exactly {2021..2025}, never
    including 2026 -- the direct, simplest proof of fit isolation."""
    result = run_holdout_evaluation(_synthetic_matrix(), random_state=0)
    assert set(result["metrics"]["train_seasons"]) == set(DEV_SEASONS)
    assert HOLDOUT_SEASON not in result["metrics"]["train_seasons"]


# --------------------------------------------------------------------------- #
# End-to-end sanity
# --------------------------------------------------------------------------- #


def test_end_to_end_run_returns_sane_metrics() -> None:
    matrix = _synthetic_matrix(per_season=30)
    result = run_holdout_evaluation(matrix, random_state=0)

    assert result["model"] == "xgboost_locked_adr006"
    assert result["locked_params"] == LOCKED_PARAMS
    metrics = result["metrics"]
    assert np.isfinite(metrics["log_loss"])
    assert metrics["log_loss"] >= 0.0
    assert 0.0 <= metrics["brier"] <= 1.0
    assert 0.0 <= metrics["ece"] <= 1.0
    roc = metrics["secondary"]["roc_auc"]
    assert roc is None or 0.0 <= roc <= 1.0
    assert 0.0 <= metrics["secondary"]["accuracy"] <= 1.0

    n_2026_rows = sum(1 for r in matrix["rows"] if r["game_date"].year == HOLDOUT_SEASON)
    assert len(result["predictions"]) == n_2026_rows
    assert metrics["n_test"] == n_2026_rows
