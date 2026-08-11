"""Baseball-specific Gold feature completeness checks (FEAT-006).

The historical gate is intentionally strict: every declared V1 family must be
present, every published column must contain at least one measurement, and core
history measurements must be populated on at least half of the 2021-2025 rows.
The 50% floor is deliberately conservative. Season openers, new starters, and
pitchers without prior MLB history legitimately produce missing pregame rates,
but those cases cannot plausibly comprise half of a five-season matrix. Falling
below that floor means a planned family is too sparse for the model to use
reliably and requires an upstream repair or an accepted methodology decision.

Constant detection is applied only at 30 or more rows: a one-day inference slate
or a focused fixture cannot establish that a feature is unexpectedly degenerate.
Historical failures never auto-drop columns. The inference mode still reports
missing/dead/sparse fields as WARN, but does not block a small current slate;
PIPE-001 continues to vectorize against its declared training-column union.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

HISTORICAL_MODE = "historical"
INFERENCE_MODE = "inference"
_MODES = frozenset({HISTORICAL_MODE, INFERENCE_MODE})

# A five-season feature should have prior-history measurements on most rows.
# Fifty percent tolerates legitimate cold starts generously while still catching
# a family that preprocessing would effectively ignore.
MIN_REQUIRED_POPULATION = 0.50
MIN_ROWS_FOR_DISTRIBUTION_CHECKS = 30

_TEAM_BASES = (
    "games_played_before", "wins_before", "losses_before", "win_pct_before",
    "runs_scored_total_before", "runs_allowed_total_before",
    "run_diff_total_before", "runs_scored_avg_before",
    "runs_allowed_avg_before", "run_diff_avg_before",
    "games_L7", "win_pct_L7", "runs_scored_avg_L7", "runs_allowed_avg_L7",
    "run_diff_avg_L7", "run_diff_total_L7",
    "games_L14", "win_pct_L14", "runs_scored_avg_L14", "runs_allowed_avg_L14",
    "run_diff_avg_L14", "run_diff_total_L14",
    "games_L30", "win_pct_L30", "runs_scored_avg_L30", "runs_allowed_avg_L30",
    "run_diff_avg_L30", "run_diff_total_L30",
)

_STARTER_BASES = (
    "starter_pitcher_id", "starter_known", "starter_is_probable",
    "season_starts_before",
    "season_ip_before", "season_era_before", "season_whip_before",
    "season_k_rate_before", "season_bb_rate_before", "season_k_bb_rate_before",
    "season_ip_per_start_before", "roll_starts_L3", "roll_era_L3",
    "roll_whip_L3", "roll_k_rate_L3", "roll_ip_per_start_L3",
    "roll_starts_L5", "roll_era_L5", "roll_whip_L5", "roll_k_rate_L5",
    "roll_ip_per_start_L5", "roll_starts_L10", "roll_era_L10",
    "roll_whip_L10", "roll_k_rate_L10", "roll_ip_per_start_L10",
)

_BULLPEN_BASES = (
    "bullpen_games_L7", "bullpen_ip_L7", "bullpen_appearances_L7",
    "bullpen_era_L7", "bullpen_whip_L7", "bullpen_games_L14",
    "bullpen_ip_L14", "bullpen_appearances_L14", "bullpen_era_L14",
    "bullpen_whip_L14", "bullpen_games_L30", "bullpen_ip_L30",
    "bullpen_appearances_L30", "bullpen_era_L30", "bullpen_whip_L30",
)

# Rest/schedule is cross-component by design: starter recovery plus recent
# bullpen workload. These fields are kept separate from pitcher quality so an
# available ERA cannot conceal an absent fatigue signal.
_REST_STARTER_BASES = (
    "days_rest", "prev_start_ip", "prev_start_pitches",
    "prev_start_batters_faced",
)
_REST_BULLPEN_BASES = (
    "bullpen_outs_prior_1d", "bullpen_ip_prior_1d",
    "bullpen_pitches_prior_1d", "bullpen_appearances_prior_1d",
    "bullpen_outs_prior_3d", "bullpen_ip_prior_3d",
    "bullpen_pitches_prior_3d", "bullpen_appearances_prior_3d",
)


def _oriented(
    component: str,
    bases: Sequence[str],
    *,
    no_differential: Sequence[str] = (),
) -> tuple[str, ...]:
    no_diff = set(no_differential)
    columns: list[str] = []
    for base in bases:
        columns.extend((f"home_{component}_{base}", f"away_{component}_{base}"))
        if base not in no_diff:
            columns.append(f"diff_{component}_{base}")
    return tuple(columns)


# Explicit V1 family contract. Adding/removing planned bases is a methodology
# change and must be reviewed, never inferred from whichever columns happen to
# have non-null values in one build.
REQUIRED_FAMILY_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "team": _oriented("team", _TEAM_BASES),
    "starter": _oriented(
        "starter", _STARTER_BASES,
        no_differential=(
            "starter_pitcher_id", "starter_known", "starter_is_probable"
        ),
    ),
    "bullpen": _oriented("bullpen", _BULLPEN_BASES),
    "rest_schedule": (
        *_oriented("starter", _REST_STARTER_BASES),
        *_oriented("bullpen", _REST_BULLPEN_BASES),
    ),
}

# These two indicators may legitimately be constant in a repaired historical
# boxscore build (all actual starters known, none resolved only from probable).
# They remain required and reported, but are not treated as broken measurements.
EXPECTED_CONSTANT_COLUMNS = frozenset(
    {
        "home_starter_starter_known", "away_starter_starter_known",
        "home_starter_starter_is_probable", "away_starter_starter_is_probable",
    }
)


class FeatureCompletenessError(ValueError):
    """Historical Gold publication failed; ``report`` retains all diagnostics."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = report
        issues = report.get("blocking_issues", [])
        summary = "; ".join(str(issue) for issue in issues[:6])
        if len(issues) > 6:
            summary += f"; ... ({len(issues) - 6} more)"
        super().__init__(
            "Gold historical feature completeness FAIL: " + summary
            + ". Repair upstream data; no broken feature was auto-dropped."
        )


def build_feature_completeness_report(
    feature_columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    *,
    mode: str = HISTORICAL_MODE,
) -> dict[str, Any]:
    """Return per-column and family completeness diagnostics for a Gold matrix."""
    if mode not in _MODES:
        raise ValueError(f"completeness_mode must be one of {sorted(_MODES)}, got {mode!r}")
    columns = list(feature_columns)
    if len(columns) != len(set(columns)):
        raise ValueError("feature_columns contains duplicates")

    row_count = len(rows)
    required_lookup = {
        column: family
        for family, family_columns in REQUIRED_FAMILY_COLUMNS.items()
        for column in family_columns
    }
    column_reports: dict[str, dict[str, Any]] = {}
    blocking: list[str] = []

    for column in columns:
        values = [(row.get("features") or {}).get(column) for row in rows]
        populated = [value for value in values if not _is_missing(value)]
        distinct = {_stable_value(value) for value in populated}
        numeric = [float(value) for value in populated if _is_number(value)]
        rate = len(populated) / row_count if row_count else 0.0
        all_null = not populated
        constant = (
            row_count >= MIN_ROWS_FOR_DISTRIBUTION_CHECKS
            and len(distinct) == 1
            and column not in EXPECTED_CONSTANT_COLUMNS
        )
        required = column in required_lookup
        low_population = (
            required
            and row_count >= MIN_ROWS_FOR_DISTRIBUTION_CHECKS
            and rate < MIN_REQUIRED_POPULATION
        )
        issues: list[str] = []
        if all_null:
            issues.append("entirely_null")
        if constant:
            issues.append("unexpected_constant")
        if low_population:
            issues.append("implausibly_low_population")
        status = _issue_status(issues, mode)
        if mode == HISTORICAL_MODE and status == "FAIL":
            blocking.extend(f"{column}: {issue}" for issue in issues)
        column_reports[column] = {
            "family": required_lookup.get(column, _family_for_column(column)),
            "required": required,
            "row_count": row_count,
            "non_null_count": len(populated),
            "null_rate": 1.0 - rate,
            "population_rate": rate,
            "distinct_non_null_count": len(distinct),
            "min": min(numeric) if numeric else None,
            "max": max(numeric) if numeric else None,
            "status": status,
            "issues": issues,
        }

    family_reports: dict[str, dict[str, Any]] = {}
    present = set(columns)
    for family, required_columns in REQUIRED_FAMILY_COLUMNS.items():
        missing = sorted(set(required_columns) - present)
        present_required = [column for column in required_columns if column in present]
        present_family = [
            column for column in columns
            if column_reports[column]["family"] == family
        ]
        all_null = [
            column for column in present_family
            if column_reports[column]["non_null_count"] == 0
        ]
        low = [
            column for column in present_required
            if column_reports[column]["population_rate"] < MIN_REQUIRED_POPULATION
            and row_count >= MIN_ROWS_FOR_DISTRIBUTION_CHECKS
        ]
        constants = [
            column for column in present_family
            if "unexpected_constant" in column_reports[column]["issues"]
        ]
        slots = row_count * len(required_columns)
        non_null = sum(column_reports[column]["non_null_count"] for column in present_required)
        population_rate = non_null / slots if slots else 0.0
        column_status = _gate_status(bool(missing), mode)
        population_problem = bool(all_null or low) or not present_required or row_count == 0
        population_status = _gate_status(population_problem, mode)
        constant_status = _gate_status(bool(constants), mode)
        statuses = (column_status, population_status, constant_status)
        family_status = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
        if mode == HISTORICAL_MODE:
            if missing:
                blocking.append(f"{family} family missing required columns: {missing}")
            if not present_required or row_count == 0:
                blocking.append(f"{family} family is entirely absent")
        family_reports[family] = {
            "row_count": row_count,
            "required_columns": list(required_columns),
            "present_required_count": len(present_required),
            "missing_required_columns": missing,
            "required_columns_status": column_status,
            "required_non_null_count": non_null,
            "population_rate": population_rate,
            "all_null_columns": all_null,
            "implausibly_low_population_columns": low,
            "population_status": population_status,
            "unexpected_constant_columns": constants,
            "constant_status": constant_status,
            "status": family_status,
        }

    # Preserve order while removing duplicate summaries emitted at column/family level.
    blocking = list(dict.fromkeys(blocking))
    has_warning = any(
        column["status"] == "WARN" for column in column_reports.values()
    ) or any(family["status"] == "WARN" for family in family_reports.values())
    return {
        "mode": mode,
        "status": "FAIL" if blocking else "WARN" if has_warning else "PASS",
        "row_count": row_count,
        "policy": {
            "minimum_required_population_rate": MIN_REQUIRED_POPULATION,
            "minimum_rows_for_distribution_checks": MIN_ROWS_FOR_DISTRIBUTION_CHECKS,
            "historical_all_null_columns": "FAIL",
            "inference_sparse_columns": "WARN (non-blocking; declared training union applies)",
            "constant_exemptions": sorted(EXPECTED_CONSTANT_COLUMNS),
        },
        "columns": column_reports,
        "families": family_reports,
        "blocking_issues": blocking,
    }


def require_historical_feature_completeness(matrix: Any) -> None:
    """Block model work on a published Gold matrix without a strict PASS gate."""
    if not isinstance(matrix, Mapping):
        return  # Bare row sequences remain supported for focused evaluator tests.
    # Only FEAT-004 publication-shaped mappings are subject to this gate. Small
    # synthetic evaluator fixtures contain only ``rows`` and remain valid.
    if "build_id" not in matrix and "certification_status" not in matrix:
        return
    report = matrix.get("feature_completeness")
    if not isinstance(report, Mapping):
        raise ValueError("ML experiments require a Gold feature_completeness report")
    if report.get("mode") != HISTORICAL_MODE or report.get("status") != "PASS":
        raise ValueError(
            "ML experiments require Gold historical feature completeness PASS; "
            f"got mode={report.get('mode')!r}, status={report.get('status')!r}"
        )


def _family_for_column(column: str) -> str:
    try:
        _orientation, component, base = column.split("_", 2)
    except ValueError:
        return "unclassified"
    if (component == "starter" and base in _REST_STARTER_BASES) or (
        component == "bullpen" and base in _REST_BULLPEN_BASES
    ):
        return "rest_schedule"
    return component if component in ("team", "starter", "bullpen") else "unclassified"


def _issue_status(issues: Sequence[str], mode: str) -> str:
    if not issues:
        return "PASS"
    return "FAIL" if mode == HISTORICAL_MODE else "WARN"


def _gate_status(problem: bool, mode: str) -> str:
    if not problem:
        return "PASS"
    return "FAIL" if mode == HISTORICAL_MODE else "WARN"


def _stable_value(value: Any) -> tuple[str, str]:
    """Hash arbitrary scalar-ish feature values without comparing unlike types."""
    return type(value).__name__, repr(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, Real) and not isinstance(value, bool) and math.isnan(float(value))
