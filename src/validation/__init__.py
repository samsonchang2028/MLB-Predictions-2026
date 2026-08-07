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

# --- DATA-009: historical odds-archive validation + mapping (append-only) ---
from validation.odds_mapping import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    ArchiveEvent,
    GameCandidate,
    OddsGameMapping,
    build_archive_events,
    build_coverage_report,
    decide_mapping,
    map_archive_events,
    normalize_team_name,
    run_odds_archive_checks,
    validate_odds_archive,
    write_coverage_report,
)

__all__ += [
    "MATCHED",
    "UNMATCHED",
    "AMBIGUOUS",
    "ArchiveEvent",
    "GameCandidate",
    "OddsGameMapping",
    "build_archive_events",
    "build_coverage_report",
    "decide_mapping",
    "map_archive_events",
    "normalize_team_name",
    "run_odds_archive_checks",
    "validate_odds_archive",
    "write_coverage_report",
]
