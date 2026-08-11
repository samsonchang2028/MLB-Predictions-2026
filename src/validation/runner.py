"""Minimal validation runner for the historical dataset (DATA-006).

``run_all`` executes every Bronze/Silver + leakage check against a DuckDB
connection and returns an ordered list of ``CheckResult``. ``certify`` collapses
that into the certification verdict DATA-007 consumes (and DATA-009 reuses).

There is deliberately no plugin registry or class hierarchy: the check list is
fixed and explicit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from validation.checks import run_data_checks
from validation.coverage import run_coverage_checks
from validation.leakage import (
    check_chronological_folds,
    check_current_game_excluded,
    check_future_mutation_invariance,
    load_team_game_rows,
)
from validation.results import CheckResult, summarize


def run_all(
    connection: Any, storage_root: str | Path | None = None
) -> list[CheckResult]:
    """Run structural, semantic-completeness, temporal, and leakage checks.

    Order is stable: structural Bronze/Silver checks, then semantic-completeness
    coverage checks (DATA-017), then temporal/leakage checks.
    """
    results = run_data_checks(connection, storage_root)
    results.extend(run_coverage_checks(connection))
    team_rows = load_team_game_rows(connection)
    results.append(check_current_game_excluded(team_rows))
    results.append(check_future_mutation_invariance(team_rows))
    results.append(check_chronological_folds())
    return results


def certify(
    connection: Any, storage_root: str | Path | None = None
) -> dict[str, object]:
    """Return the certification verdict for the current dataset."""
    return summarize(run_all(connection, storage_root))
