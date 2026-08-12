"""Unit tests for the APP-002 performance dashboard data-loading/shaping module.

Exercises: correct shaping/labeling of the repaired-report ranking and
calibration data, presence of the development-evidence label on every row,
verbatim journal pass-through, and -- as a real assertion, not
absence-by-omission -- that the loader functions never read the report's
``holdout_2026`` field even when it is present in the input dict.

No Streamlit import here -- this module is the testable half of APP-002.
"""

from __future__ import annotations

from app.performance import (
    DEVELOPMENT_EVIDENCE_LABEL,
    load_calibration_comparison,
    load_model_window_ranking,
    load_prediction_history,
)


class _NoHoldoutAccessDict(dict):
    """A dict that fails the test the moment ``holdout_2026`` is looked up.

    Wraps a real repaired-report-shaped dict (which legitimately carries a
    ``holdout_2026`` key, per the real committed reports) and asserts nothing
    under test ever reads it -- via ``[]``, ``.get``, or ``in``.
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
    # _NoHoldoutAccessDict raises if the report's holdout_2026 key is touched.
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


def test_prediction_history_no_2026_fields_present_in_fixture_or_output():
    # Regression guard: journal records used here (and in real OBS-001 output)
    # never carry a season/2026 field at all; the loader must not invent one.
    record = _journal_record(9)
    assert not any("2026" in str(key) for key in record)
    [row] = load_prediction_history(_FakeJournalStore([record]))
    assert not any("2026" in str(key) for key in row)
