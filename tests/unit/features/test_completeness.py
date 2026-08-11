"""Gold pre-model semantic completeness regression tests (FEAT-006)."""

from __future__ import annotations

import pytest

from features.completeness import (
    EXPECTED_CONSTANT_COLUMNS,
    REQUIRED_FAMILY_COLUMNS,
    FeatureCompletenessError,
    build_feature_completeness_report,
    require_historical_feature_completeness,
)


def _columns() -> list[str]:
    return sorted({column for columns in REQUIRED_FAMILY_COLUMNS.values() for column in columns})


def _rows(count: int = 40) -> list[dict]:
    columns = _columns()
    rows = []
    for index in range(count):
        features = {
            column: (True if column in EXPECTED_CONSTANT_COLUMNS else index + offset / 1000)
            for offset, column in enumerate(columns)
        }
        rows.append({"game_pk": index + 1, "features": features})
    return rows


def test_complete_historical_report_exposes_columns_and_families() -> None:
    columns = _columns()
    report = build_feature_completeness_report(columns, _rows())

    assert report["status"] == "PASS"
    assert set(report["families"]) == {"team", "starter", "bullpen", "rest_schedule"}
    for family in report["families"].values():
        assert family["required_columns_status"] == "PASS"
        assert family["population_status"] == "PASS"
        assert family["status"] == "PASS"
    sample = report["columns"]["home_starter_season_era_before"]
    assert sample["row_count"] == 40
    assert sample["non_null_count"] == 40
    assert sample["null_rate"] == 0.0
    assert sample["min"] is not None and sample["max"] is not None


def test_entirely_null_published_column_hard_fails_and_is_not_dropped() -> None:
    columns = [*_columns(), "home_team_dead_upstream_measure"]
    rows = _rows()
    for index, row in enumerate(rows):
        # Both Python null and model-facing NaN mean no usable measurement.
        row["features"]["home_team_dead_upstream_measure"] = (
            None if index % 2 else float("nan")
        )

    report = build_feature_completeness_report(columns, rows)

    dead = report["columns"]["home_team_dead_upstream_measure"]
    assert report["status"] == "FAIL"
    assert dead["status"] == "FAIL"
    assert dead["issues"] == ["entirely_null"]
    assert "home_team_dead_upstream_measure" in report["columns"]
    assert report["families"]["team"]["status"] == "FAIL"


def test_absent_required_family_fails_with_named_family() -> None:
    bullpen = set(REQUIRED_FAMILY_COLUMNS["bullpen"])
    columns = [column for column in _columns() if column not in bullpen]
    rows = _rows()
    for row in rows:
        row["features"] = {key: value for key, value in row["features"].items() if key in columns}

    report = build_feature_completeness_report(columns, rows)

    family = report["families"]["bullpen"]
    assert report["status"] == "FAIL"
    assert family["required_columns_status"] == "FAIL"
    assert family["population_status"] == "FAIL"
    assert family["present_required_count"] == 0
    assert any("bullpen family" in issue for issue in report["blocking_issues"])


def test_unexpected_constant_column_is_detected_and_blocks_historical_gate() -> None:
    rows = _rows()
    column = "home_bullpen_bullpen_era_L7"
    for row in rows:
        row["features"][column] = 0.0

    report = build_feature_completeness_report(_columns(), rows)

    assert report["status"] == "FAIL"
    assert report["columns"][column]["issues"] == ["unexpected_constant"]
    assert column in report["families"]["bullpen"]["unexpected_constant_columns"]


def test_documented_legitimate_sparsity_above_half_passes() -> None:
    rows = _rows()
    column = "home_starter_season_era_before"
    # Sixteen cold-start rows out of forty leave 60% populated, above the
    # documented conservative historical floor.
    for row in rows[:16]:
        row["features"][column] = None

    report = build_feature_completeness_report(_columns(), rows)

    assert report["status"] == "PASS"
    assert report["columns"][column]["population_rate"] == pytest.approx(0.6)


def test_implausibly_low_required_population_fails() -> None:
    rows = _rows()
    column = "home_starter_season_era_before"
    for row in rows[:24]:
        row["features"][column] = None

    report = build_feature_completeness_report(_columns(), rows)

    assert report["status"] == "FAIL"
    assert "implausibly_low_population" in report["columns"][column]["issues"]
    assert column in report["families"]["starter"]["implausibly_low_population_columns"]


def test_inference_sparsity_warns_without_blocking_small_slate() -> None:
    columns = _columns()
    rows = _rows(count=1)
    dead = "home_starter_season_era_before"
    rows[0]["features"][dead] = None

    report = build_feature_completeness_report(columns, rows, mode="inference")

    assert report["status"] == "WARN"
    assert report["columns"][dead]["status"] == "WARN"
    assert report["families"]["starter"]["population_status"] == "WARN"


def test_ml_gate_refuses_inference_or_failed_published_matrix() -> None:
    inference = build_feature_completeness_report(_columns(), _rows(1), mode="inference")
    matrix = {
        "build_id": "certified-build",
        "certification_status": "PASS",
        "feature_completeness": inference,
        "rows": [],
    }
    with pytest.raises(ValueError, match="historical feature completeness PASS"):
        require_historical_feature_completeness(matrix)


# --- FEAT-005/006 tester edge cases (adversarial, not part of the original PR) --


def test_near_zero_but_nonzero_population_is_low_not_entirely_null() -> None:
    # Two populated rows (with different values, so this isn't ALSO flagged
    # unexpected_constant) out of a thousand: not literally "entirely_null"
    # (that issue requires zero populated values), but nowhere near the 50%
    # floor.
    columns = _columns()
    rows = _rows(count=1000)
    column = "home_starter_season_era_before"
    for row in rows[2:]:
        row["features"][column] = None

    report = build_feature_completeness_report(columns, rows)

    entry = report["columns"][column]
    assert entry["non_null_count"] == 2
    assert entry["issues"] == ["implausibly_low_population"]
    assert "entirely_null" not in entry["issues"]
    assert report["status"] == "FAIL"


def test_ninety_nine_percent_null_family_fails_as_implausibly_low() -> None:
    # Not entirely empty (so the "hollow build" all_null signature does not
    # apply), but 1% population is far below any legitimate cold-start rate for
    # a five-season matrix. The gate must still hard-block, not merely warn,
    # per the documented 50% floor -- and other, healthy families must stay PASS.
    columns = _columns()
    rows = _rows(count=200)
    column = "home_starter_season_era_before"
    for row in rows[:198]:
        row["features"][column] = None

    report = build_feature_completeness_report(columns, rows)

    assert report["columns"][column]["population_rate"] == pytest.approx(0.01)
    assert report["columns"][column]["issues"] == ["implausibly_low_population"]
    assert report["families"]["starter"]["status"] == "FAIL"
    assert report["families"]["team"]["status"] == "PASS"
    assert report["families"]["bullpen"]["status"] == "PASS"
    assert report["status"] == "FAIL"


def test_feature_completeness_error_retains_full_report() -> None:
    columns = _columns()
    rows = _rows()
    rows[0]["features"][columns[0]] = None
    for row in rows[1:]:
        row["features"][columns[0]] = None
    report = build_feature_completeness_report(columns, rows)

    error = FeatureCompletenessError(report)
    assert error.report is report
    assert "upstream" in str(error)
