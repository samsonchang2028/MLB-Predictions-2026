"""Historical MLB data validation (DATA-006).

Public surface consumed by the DATA-007 certification gate and reused by
DATA-009: run the checks, then read the ``summarize``/``certify`` verdict.
"""

from validation.leakage import (
    ALL_FOLDS,
    DEV_SEASONS,
    HOLDOUT_SEASON,
    check_chronological_folds,
    check_current_game_excluded,
    check_future_mutation_invariance,
    load_team_game_rows,
)
from validation.results import (
    FAIL,
    PASS,
    WARN,
    CheckResult,
    summarize,
)
from validation.runner import certify, run_all

__all__ = [
    "CheckResult",
    "PASS",
    "FAIL",
    "WARN",
    "summarize",
    "run_all",
    "certify",
    "load_team_game_rows",
    "check_chronological_folds",
    "check_current_game_excluded",
    "check_future_mutation_invariance",
    "ALL_FOLDS",
    "DEV_SEASONS",
    "HOLDOUT_SEASON",
]
