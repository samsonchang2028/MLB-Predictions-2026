"""Temporal / leakage validation (ADR-002, ADR-003).

These checks exercise the real feature builder to prove point-in-time safety:

- ``check_future_mutation_invariance`` — build features, add a FUTURE game,
  rebuild, and assert every earlier feature row is unchanged (the required
  future-mutation regression).
- ``check_current_game_excluded`` — mutating a game's own result must not change
  that game's pregame features.
- ``check_chronological_folds`` — expanding/rolling folds (ADR-003) are
  chronological, non-overlapping, and never let 2026 enter development
  selection.

Leakage findings are severity P0 so they are always merge-blocking (ADR-002).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from features.team import build_team_features
from validation.results import CheckResult, fail, ok, warn

BuildFn = Callable[[Sequence[Mapping[str, Any]]], list[dict[str, Any]]]

# ADR-003 development seasons and untouched final holdout.
DEV_SEASONS = ("2021", "2022", "2023", "2024", "2025")
HOLDOUT_SEASON = "2026"

# ADR-003 chronological folds as (train_seasons, test_season).
EXPANDING_FOLDS = (
    (("2021",), "2022"),
    (("2021", "2022"), "2023"),
    (("2021", "2022", "2023"), "2024"),
    (("2021", "2022", "2023", "2024"), "2025"),
)
ROLLING_2_FOLDS = (
    (("2021", "2022"), "2023"),
    (("2022", "2023"), "2024"),
    (("2023", "2024"), "2025"),
)
ROLLING_3_FOLDS = (
    (("2021", "2022", "2023"), "2024"),
    (("2022", "2023", "2024"), "2025"),
)
ALL_FOLDS = EXPANDING_FOLDS + ROLLING_2_FOLDS + ROLLING_3_FOLDS


def _feature_map(rows: Sequence[Mapping[str, Any]], build: BuildFn) -> dict[tuple, dict]:
    return {(r["game_pk"], r["team_id"]): r for r in build(rows)}


def load_team_game_rows(connection: Any) -> list[dict[str, Any]]:
    """Load ``build_team_features`` input from Silver (empty if Silver is absent)."""
    exists = connection.execute(
        """SELECT count(*) FROM information_schema.tables
           WHERE table_schema = 'silver' AND table_name = 'team_game_statistics'"""
    ).fetchone()[0]
    if not exists:
        return []
    rows = connection.execute(
        """SELECT t.game_pk, t.team_id, t.side, t.game_date, g.season,
                  t.score, t.is_winner
           FROM silver.team_game_statistics t
           JOIN silver.games g USING (game_pk)
           ORDER BY t.game_pk, t.team_id"""
    ).fetchall()
    return [
        {
            "game_pk": game_pk,
            "team_id": team_id,
            "side": side,
            "game_date": game_date,
            "season": None if season is None else str(season),
            "score": score,
            "is_winner": is_winner,
        }
        for game_pk, team_id, side, game_date, season, score, is_winner in rows
    ]


def check_future_mutation_invariance(
    rows: Sequence[Mapping[str, Any]], build: BuildFn = build_team_features
) -> CheckResult:
    """Adding a strictly-future completed game must not change any earlier feature."""
    check = "leakage.future_mutation_invariance"
    rows = list(rows)
    future = _synthetic_future_pair(rows)
    if future is None:
        return warn(check, "no team-game rows to validate", severity="P0")

    try:
        before = _feature_map(rows, build)
        after = _feature_map(list(rows) + future, build)
    except ValueError as error:
        return fail(check, "P0", f"feature build rejected the data: {error}")
    changed = [key for key, value in before.items() if after.get(key) != value]
    if changed:
        return fail(check, "P0", "future game altered earlier features", changed)
    return ok(check, "P0")


def check_current_game_excluded(
    rows: Sequence[Mapping[str, Any]], build: BuildFn = build_team_features
) -> CheckResult:
    """Mutating a game's own result must not change that game's pregame features."""
    check = "leakage.current_game_excluded"
    rows = list(rows)
    target = _last_completed_game(rows)
    if target is None:
        return warn(check, "no completed game to validate", severity="P0")

    mutated = [dict(r) for r in rows]
    for row in mutated:
        if row["game_pk"] == target:
            # Flip the recorded outcome for this game only.
            if row["side"] == "home":
                row["score"], row["is_winner"] = 99, True
            else:
                row["score"], row["is_winner"] = 0, False
    try:
        before = _feature_map(rows, build)
        after = _feature_map(mutated, build)
    except ValueError as error:
        return fail(check, "P0", f"feature build rejected the data: {error}")
    changed = [
        (game_pk, team_id)
        for (game_pk, team_id), value in before.items()
        if game_pk == target and after.get((game_pk, team_id)) != value
    ]
    if changed:
        return fail(check, "P0", "current game's own result leaked into its features", changed)
    return ok(check, "P0")


def check_chronological_folds(
    folds: Sequence[tuple[Sequence[str], str]] = ALL_FOLDS,
    dev_seasons: Sequence[str] = DEV_SEASONS,
    holdout_season: str = HOLDOUT_SEASON,
) -> CheckResult:
    """Folds must be chronological, non-overlapping, and holdout-free."""
    check = "leakage.chronological_folds"
    problems: list[object] = []
    if holdout_season in dev_seasons:
        problems.append((holdout_season, "holdout_in_dev_seasons"))
    for train, test in folds:
        train_years = [int(s) for s in train]
        test_year = int(test)
        if test in train:
            problems.append((test, "train_test_overlap"))
        if train_years and max(train_years) >= test_year:
            problems.append((test, "non_chronological_train"))
        if holdout_season == test or holdout_season in train:
            problems.append((test, "holdout_in_selection"))
    if problems:
        return fail(check, "P0", "invalid fold configuration", problems)
    return ok(check, "P0")


def _synthetic_future_pair(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """A brand-new completed game strictly after everything in ``rows``."""
    if not rows:
        return None
    last = max(rows, key=lambda r: (r["game_date"], r["game_pk"]))
    game_date = last["game_date"] + timedelta(days=1)
    game_pk = max(r["game_pk"] for r in rows) + 1
    team_ids = sorted({r["team_id"] for r in rows})
    home_id = team_ids[0]
    away_id = team_ids[-1] if len(team_ids) > 1 else team_ids[0] + 1
    season = last.get("season")
    return [
        _future_row(game_pk, home_id, "home", game_date, season, 10, True),
        _future_row(game_pk, away_id, "away", game_date, season, 0, False),
    ]


def _future_row(
    game_pk: int, team_id: int, side: str, game_date: Any, season: Any, score: int, won: bool
) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": game_date,
        "season": season,
        "score": score,
        "is_winner": won,
    }


def _last_completed_game(rows: Sequence[Mapping[str, Any]]) -> Any:
    """The latest game_pk whose two rows both have integer scores and a winner."""
    by_game: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_game.setdefault(row["game_pk"], []).append(row)
    completed = [
        (game_rows[0]["game_date"], game_pk)
        for game_pk, game_rows in by_game.items()
        if len(game_rows) == 2
        and all(isinstance(r["score"], int) and not isinstance(r["score"], bool) for r in game_rows)
        and any(r["is_winner"] for r in game_rows)
    ]
    if not completed:
        return None
    return max(completed)[1]
