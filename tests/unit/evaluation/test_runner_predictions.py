"""Tests for the opt-in per-fold prediction table (ML-004A).

Verifies that ``run_evaluation(..., return_predictions=True)`` emits a
``game_pk``-keyed prediction list per fold that is correctly aligned to the exact
probabilities/labels backing that fold's metrics (i.e. after the identical
unlabeled-drop), deterministic, and absent by default.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss

from evaluation.runner import run_evaluation
from evaluation.splits import Fold, expanding_folds
from models import logistic


def _dt(year: int, month: int = 4, day: int = 1) -> datetime:
    return datetime(year, month, day, 19, 0, tzinfo=timezone.utc)


# game_pk that is deliberately UNLABELED (home_win=None) in its test season, used
# to exercise the unlabeled-drop alignment.
_UNLABELED_GAME_PK = 999999


def _synthetic_matrix(per_season: int = 40, seed: int = 0) -> dict:
    """Learnable 2021-2025 matrix with KNOWN game_pks.

    One extra row in the 2025 test season carries an undecided (``None``) label so
    the unlabeled-drop path is exercised for a test fold.
    """
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

    # An UNLABELED test row in the final test season (2025).
    rows.append(
        {
            "game_pk": _UNLABELED_GAME_PK,
            "game_date": _dt(2025, day=28),
            "prediction_timestamp": _dt(2025),
            "home_team_id": 10,
            "away_team_id": 20,
            "features": {
                "home_team_strength": 0.1,
                "away_team_strength": -0.1,
                "diff_team_strength": 0.2,
            },
            "target": {"home_win": None},
        }
    )
    return {"feature_columns": [], "rows": rows}


def _labels_by_game_pk(matrix: dict) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    for row in matrix["rows"]:
        hw = row["target"]["home_win"]
        out[row["game_pk"]] = None if hw is None else int(hw)
    return out


def test_default_call_has_no_predictions_key_and_matches_prior_behavior() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()

    default = run_evaluation(logistic, matrix, folds, random_state=0)
    with_preds = run_evaluation(
        logistic, matrix, folds, random_state=0, return_predictions=True
    )

    for fold in default["folds"]:
        assert "predictions" not in fold

    # Existing per-fold keys are byte-for-byte identical with/without the flag.
    for base, extra in zip(default["folds"], with_preds["folds"]):
        for key, value in base.items():
            assert extra[key] == value
    assert default["aggregate"] == with_preds["aggregate"]


def test_predictions_length_and_game_pk_sequence_match_labeled_test_rows() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    report = run_evaluation(
        logistic, matrix, folds, random_state=0, return_predictions=True
    )

    for fold_report, fold in zip(report["folds"], folds):
        preds = fold_report["predictions"]
        # length == labeled test-row count reported by the metrics.
        assert len(preds) == fold_report["n_test"]

        # Expected labeled test game_pks in the deterministic (game_date, game_pk)
        # order used by the runner.
        test_rows = [
            r
            for r in matrix["rows"]
            if r["game_date"].year == fold.test_season
            and r["target"]["home_win"] is not None
        ]
        test_rows.sort(key=lambda r: (r["game_date"], r["game_pk"]))
        expected_pks = [r["game_pk"] for r in test_rows]

        got_pks = [p["game_pk"] for p in preds]
        assert got_pks == expected_pks


def test_probabilities_in_range_and_back_the_fold_metrics() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    report = run_evaluation(
        logistic, matrix, folds, random_state=0, return_predictions=True
    )

    for fold_report in report["folds"]:
        preds = fold_report["predictions"]
        p = np.array([entry["p_home_win"] for entry in preds], dtype=float)
        y = np.array([entry["y_true"] for entry in preds], dtype=int)

        assert np.all((p >= 0.0) & (p <= 1.0))

        # Recomputing the primary metrics from the returned predictions must match
        # the fold's reported metrics -> the returned p vector is the SAME vector
        # that backs the fold metrics.
        assert log_loss(y, p, labels=[0, 1]) == fold_report["log_loss"]
        assert brier_score_loss(y, p, pos_label=1) == fold_report["brier"]


def test_y_true_matches_matrix_home_win() -> None:
    matrix = _synthetic_matrix()
    labels = _labels_by_game_pk(matrix)
    report = run_evaluation(
        logistic, matrix, expanding_folds(), random_state=0, return_predictions=True
    )

    for fold_report in report["folds"]:
        for entry in fold_report["predictions"]:
            assert entry["y_true"] == labels[entry["game_pk"]]


def test_unlabeled_test_row_is_absent_from_predictions() -> None:
    matrix = _synthetic_matrix()
    # The unlabeled row lives in the 2025 test season -> exercise that fold.
    report = run_evaluation(
        logistic,
        matrix,
        [Fold((2021, 2022, 2023, 2024), 2025)],
        random_state=0,
        return_predictions=True,
    )
    all_pks = {e["game_pk"] for e in report["folds"][0]["predictions"]}
    assert _UNLABELED_GAME_PK not in all_pks


def test_predictions_are_deterministic_across_calls() -> None:
    matrix = _synthetic_matrix()
    folds = expanding_folds()
    a = run_evaluation(
        logistic, matrix, folds, random_state=0, return_predictions=True
    )
    b = run_evaluation(
        logistic, matrix, folds, random_state=0, return_predictions=True
    )
    assert [f["predictions"] for f in a["folds"]] == [
        f["predictions"] for f in b["folds"]
    ]
