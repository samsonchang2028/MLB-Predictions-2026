"""Deterministic chronological (walk-forward) fold definitions (ML-004, ADR-003).

Folds are keyed by *season*. A :class:`Fold` names an ordered tuple of training
seasons and a single held-out test season. Two development schemes are provided:

Expanding window (ADR-003)
    train 2021 -> test 2022; train 2021-2022 -> test 2023;
    train 2021-2023 -> test 2024; train 2021-2024 -> test 2025.

Rolling windows (ADR-003)
    A fixed ``history`` of the most recent seasons immediately preceding the test
    season. To keep the 2-season and 3-season histories directly comparable, both
    are evaluated on the **same** test seasons -- the seasons for which a full
    ``_ROLLING_MAX_HISTORY``-season history of development seasons exists
    ({2024, 2025} for the 2021-2025 development span). This reproduces the ADR /
    task enumeration exactly:

        2-season: train {2022,2023} -> test 2024; train {2023,2024} -> test 2025
        3-season: train {2021,2022,2023} -> test 2024;
                  train {2022,2023,2024} -> test 2025

Constraints enforced here (ML-004 critical constraints):

- no random primary split (folds are fixed, deterministic, stably ordered),
- train seasons strictly precede the test season (chronological),
- a season never appears in both train and test of the same fold,
- the 2026 final holdout is excluded from every development fold.

:func:`fold_indices` maps a season-keyed fold onto concrete row indices for a
vectorized matrix and re-asserts the no-overlap / chronology / holdout-exclusion
invariants against the *actual* row seasons, so the runner cannot silently leak.
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

# 2021-2025 are the V1 development seasons; 2026 is the untouched final holdout.
DEV_SEASONS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
HOLDOUT_SEASON: int = 2026

# Largest rolling history compared in V1; rolling schemes share the test seasons
# for which this many preceding development seasons exist (fair comparison).
_ROLLING_MAX_HISTORY: int = 3


class Fold(NamedTuple):
    """One walk-forward fold: training seasons (ascending) and a test season."""

    train_seasons: tuple[int, ...]
    test_season: int


def expanding_folds(seasons: Sequence[int] = DEV_SEASONS) -> list[Fold]:
    """Expanding-window folds: each test season trains on all prior seasons."""
    ordered = _dev_seasons(seasons)
    folds = [
        Fold(train_seasons=ordered[:i], test_season=ordered[i])
        for i in range(1, len(ordered))
    ]
    _validate_folds(folds)
    return folds


def rolling_folds(
    history: int, seasons: Sequence[int] = DEV_SEASONS
) -> list[Fold]:
    """Rolling-window folds using the ``history`` seasons before each test season.

    ``history`` must be a positive int no larger than ``_ROLLING_MAX_HISTORY``.
    All rolling histories share a common set of test seasons so window sizes can
    be compared head-to-head on identical folds.
    """
    if not isinstance(history, int) or history < 1:
        raise ValueError(f"history must be a positive int, got {history!r}")
    if history > _ROLLING_MAX_HISTORY:
        raise ValueError(
            f"history={history} exceeds supported maximum {_ROLLING_MAX_HISTORY}"
        )
    ordered = _dev_seasons(seasons)
    season_set = set(ordered)
    test_seasons = [
        t
        for t in ordered
        if all((t - k) in season_set for k in range(1, _ROLLING_MAX_HISTORY + 1))
    ]
    folds = [
        Fold(
            train_seasons=tuple(t - k for k in range(history, 0, -1)),
            test_season=t,
        )
        for t in test_seasons
    ]
    _validate_folds(folds)
    return folds


def development_folds() -> dict[str, list[Fold]]:
    """All development schemes, keyed by a stable scheme name (deterministic)."""
    return {
        "expanding": expanding_folds(),
        "rolling_2": rolling_folds(2),
        "rolling_3": rolling_folds(3),
    }


def fold_indices(
    fold: Fold, row_seasons: Sequence[int]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Resolve a season-keyed fold to (train_indices, test_indices).

    Re-asserts, against the concrete per-row seasons, that:

    - the 2026 holdout appears in neither train nor test,
    - all training rows come from seasons strictly before the test season,
    - train and test index sets are disjoint (no row is in both).

    Rows whose season is neither a training season nor the test season are simply
    excluded from this fold.
    """
    _validate_folds([fold])
    train_seasons = set(fold.train_seasons)
    test_season = fold.test_season

    train_idx = tuple(
        i for i, s in enumerate(row_seasons) if s in train_seasons
    )
    test_idx = tuple(i for i, s in enumerate(row_seasons) if s == test_season)

    # Holdout must never enter a development fold.
    if HOLDOUT_SEASON == test_season or HOLDOUT_SEASON in train_seasons:
        raise ValueError(
            f"holdout season {HOLDOUT_SEASON} present in development fold {fold}"
        )
    # Chronology: every training row precedes the test season.
    for i in train_idx:
        if not row_seasons[i] < test_season:
            raise ValueError(
                f"train row season {row_seasons[i]} not before test season "
                f"{test_season} in fold {fold}"
            )
    # No overlap between train and test rows.
    if set(train_idx) & set(test_idx):
        raise ValueError(f"train/test row overlap in fold {fold}")
    return train_idx, test_idx


def _dev_seasons(seasons: Sequence[int]) -> tuple[int, ...]:
    """Deterministically ordered, de-duplicated development seasons (no holdout)."""
    ordered = tuple(sorted(set(seasons)))
    if HOLDOUT_SEASON in ordered:
        raise ValueError(
            f"holdout season {HOLDOUT_SEASON} must not appear in development seasons"
        )
    return ordered


def _validate_folds(folds: Sequence[Fold]) -> None:
    """Enforce the fold invariants (chronology, no overlap, holdout excluded)."""
    for fold in folds:
        train = fold.train_seasons
        test = fold.test_season
        if not train:
            raise ValueError(f"fold {fold} has no training seasons")
        if tuple(sorted(set(train))) != train:
            raise ValueError(f"fold {fold} train seasons not ascending/unique")
        if test in train:
            raise ValueError(f"fold {fold} test season also in train seasons")
        if any(s >= test for s in train):
            raise ValueError(f"fold {fold} has a train season not before test")
        if test == HOLDOUT_SEASON or HOLDOUT_SEASON in train:
            raise ValueError(f"fold {fold} references the {HOLDOUT_SEASON} holdout")
