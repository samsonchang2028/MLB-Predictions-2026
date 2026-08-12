"""Unit tests for the APP-002 performance dashboard data-loading/shaping module.

Exercises: correct shaping/labeling of the final ML-010 holdout report,
development model/window ranking, calibration data, verbatim journal
pass-through, and clear separation between development evidence and final
holdout evidence.

No Streamlit import here -- this module is the testable half of APP-002.
"""

from __future__ import annotations

from app.performance import (
    DEVELOPMENT_EVIDENCE_LABEL,
    FINAL_HOLDOUT_LABEL,
    load_calibration_comparison,
    load_holdout_predictions,
    load_holdout_summary,
    load_model_window_ranking,
    load_prediction_history,
)


class _NoHoldoutAccessDict(dict):
    """A dict that fails the test the moment ``holdout_2026`` is looked up.

    Development-report loaders should use development evidence only. A final
    holdout report now exists, but it is loaded through explicit ML-010 report
    loaders, not through the old repaired-report ``holdout_2026`` placeholder.
    """

    def __getitem__(self, key):
        assert key != "holdout_2026", "must never index report['holdout_2026']"
        return super().__getitem__(key)

    def get(self, key, default=None):
        assert key != "holdout_2026", "must never .get() report['holdout_2026']"
        return super().get(key, default)

    def __contains__(self, key):
        assert key != "holdout_2026", "must never check 'holdout_2026' in report"
        return super().__contains__(key)


class _FakeJournalStore:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _report(**overrides):
    base = {
        "holdout_2026": "not inspected",
        "ranking": [
            {
                "model": "xgboost",
                "window": "expanding",
                "log_loss": 0.68551,
                "brier": 0.24616,
                "ece": 0.0298,
                "roc_auc": 0.5695,
                "accuracy": 0.5457,
                "n_test": 4847,
                "test_seasons": [2024, 2025],
            },
            {
                "model": "random_forest",
                "window": "expanding",
                "log_loss": 0.68613,
                "brier": 0.24641,
                "ece": 0.0231,
                "roc_auc": 0.5703,
                "accuracy": 0.5544,
                "n_test": 4847,
                "test_seasons": [2024, 2025],
            },
        ],
        "calibration": {
            "variants": {
                "uncalibrated": {
                    "aggregate": {
                        "log_loss": 0.68756,
                        "brier": 0.24697,
                        "ece": 0.0350,
                        "n_test": 9694,
                    }
                },
                "sigmoid": {
                    "aggregate": {
                        "log_loss": 0.68385,
                        "brier": 0.24538,
                        "ece": 0.00554,
                        "n_test": 9694,
                    }
                },
                "isotonic": {
                    "aggregate": {
                        "log_loss": 0.72223,
                        "brier": 0.24718,
                        "ece": 0.0274,
                        "n_test": 9694,
                    }
                },
            }
        },
    }
    base.update(overrides)
    return _NoHoldoutAccessDict(base)


def _holdout_report(**overrides):
    base = {
        "status": "V1_2026_FINAL_HOLDOUT_EVALUATION",
        "model": "xgboost_locked_adr006",
        "build_id": "db7dbc8b8a1c5ae9",
        "certification_artifact": "state/data-certifications/certification-PASS.json",
        "n_rows": 13915,
        "n_feature_columns": 240,
        "excluded_games": [{"game_pk": 1}, {"game_pk": 2}],
        "gold_feature_completeness": {"status": "PASS"},
        "metrics": {
            "log_loss": 0.6887796135101659,
            "brier": 0.24781049460286877,
            "ece": 0.022240705701066947,
            "n_train": 12118,
            "n_test": 1797,
            "train_seasons": [2021, 2022, 2023, 2024, 2025],
            "test_season": 2026,
            "test_positive_rate": 0.5258764607679466,
            "secondary": {
                "roc_auc": 0.549682042874531,
                "accuracy": 0.5381190873678353,
            },
        },
        "predictions": [
            {"game_pk": 20, "p_home_win": 0.44, "y_true": 0},
            {"game_pk": 10, "p_home_win": 0.61, "y_true": 1},
        ],
    }
    base.update(overrides)
    return base


def _journal_record(game_pk, *, correct=True, model_probability=0.55):
    return {
        "game_pk": game_pk,
        "prediction_timestamp": "2024-04-01T14:00:00+00:00",
        "model_version": "v1",
        "model_probability": model_probability,
        "predicted_home_win": True,
        "actual_home_win": correct,
        "correct": correct,
    }


# --------------------------------------------------------------------------- #
# Final holdout
# --------------------------------------------------------------------------- #
def test_holdout_summary_passes_through_final_metrics_and_labels():
    [row] = load_holdout_summary(_holdout_report())
    assert row["model"] == "xgboost_locked_adr006"
    assert row["log_loss"] == 0.6887796135101659
    assert row["brier"] == 0.24781049460286877
    assert row["ece"] == 0.022240705701066947
    assert row["roc_auc"] == 0.549682042874531
    assert row["accuracy"] == 0.5381190873678353
    assert row["n_test"] == 1797
    assert row["test_season"] == 2026
    assert row["feature_completeness"] == "PASS"
    assert row["excluded_games"] == 2
    assert row["evidence_label"] == FINAL_HOLDOUT_LABEL


def test_holdout_predictions_pass_through_and_sort_by_game_pk():
    rows = load_holdout_predictions(_holdout_report())
    assert [row["game_pk"] for row in rows] == [10, 20]
    assert rows[0]["p_home_win"] == 0.61
    assert rows[0]["y_true"] == 1
    assert all(row["evidence_label"] == FINAL_HOLDOUT_LABEL for row in rows)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def test_ranking_rows_pass_through_metrics_verbatim_and_preserve_order():
    report = _report()
    rows = load_model_window_ranking(report)
    assert [r["model"] for r in rows] == ["xgboost", "random_forest"]
    assert rows[0]["log_loss"] == report["ranking"][0]["log_loss"]
    assert rows[0]["brier"] == report["ranking"][0]["brier"]
    assert rows[0]["ece"] == report["ranking"][0]["ece"]
    assert rows[0]["window"] == "expanding"


def test_ranking_rows_are_all_labeled_development_evidence():
    rows = load_model_window_ranking(_report())
    assert rows
    assert all(row["evidence_label"] == DEVELOPMENT_EVIDENCE_LABEL for row in rows)


def test_ranking_never_reads_holdout_2026_field():
    # _NoHoldoutAccessDict raises if the old repaired-report holdout_2026 key is touched.
    load_model_window_ranking(_report())


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
def test_calibration_rows_pass_through_aggregate_metrics_verbatim():
    report = _report()
    rows = load_calibration_comparison(report)
    by_method = {r["method"]: r for r in rows}
    assert by_method["sigmoid"]["log_loss"] == 0.68385
    assert by_method["sigmoid"]["ece"] == 0.00554
    assert by_method["isotonic"]["log_loss"] == 0.72223
    assert by_method["uncalibrated"]["brier"] == 0.24697


def test_calibration_rows_follow_fixed_display_order():
    rows = load_calibration_comparison(_report())
    assert [r["method"] for r in rows] == ["uncalibrated", "sigmoid", "isotonic"]


def test_calibration_skips_missing_variants_without_erroring():
    report = _report()
    del report["calibration"]["variants"]["isotonic"]
    rows = load_calibration_comparison(report)
    assert [r["method"] for r in rows] == ["uncalibrated", "sigmoid"]


def test_calibration_rows_are_all_labeled_development_evidence():
    rows = load_calibration_comparison(_report())
    assert rows
    assert all(row["evidence_label"] == DEVELOPMENT_EVIDENCE_LABEL for row in rows)


def test_calibration_never_reads_holdout_2026_field():
    load_calibration_comparison(_report())


# --------------------------------------------------------------------------- #
# Prediction history (OBS-001 journal)
# --------------------------------------------------------------------------- #
def test_prediction_history_passes_through_fields_verbatim():
    record = _journal_record(5, correct=True, model_probability=0.61)
    [row] = load_prediction_history(_FakeJournalStore([record]))
    assert row["game_pk"] == 5
    assert row["model_probability"] == 0.61
    assert row["correct"] is True
    assert row["actual_home_win"] == record["actual_home_win"]
    assert row["predicted_home_win"] == record["predicted_home_win"]


def test_prediction_history_sorted_by_game_pk():
    rows = load_prediction_history(
        _FakeJournalStore(
            [_journal_record(3), _journal_record(1), _journal_record(2)]
        )
    )
    assert [r["game_pk"] for r in rows] == [1, 2, 3]
