"""Point-in-time-safe team strength and recent-form features.

Prediction-time semantics (ADR-002)
-----------------------------------
Each output row is keyed by ``(game_pk, team_id)`` and describes that team's
state *before* the game starts. Post-game fields from
``silver.team_game_statistics`` (``score``, ``is_winner``, ``league_*``) are
never used as inputs for the same game. They may contribute only to *later*
games feature rows, after an explicit shift.

Ordering: within each ``team_id``, games are sorted by
``(game_date, game_pk)``. Rolling windows use only prior completed games
(shift-before-rolling). Home/away orientation is the Silver ``side`` value.

Cold start / window edges
-------------------------
- Season opener (no prior games in that season): rate metrics are ``None``,
  totals and ``games_played_before`` are ``0``.
- Rolling windows use up to ``N`` prior completed games (partial windows
  allowed). When zero prior completed games exist, rolling rates are ``None``
  and ``games_L{N}`` is ``0``.

Omitted Silver fields
---------------------
OPS/OBP/SLG, K%, BB%, and similar offense metrics are unavailable in the
current Silver contract; V1 offense proxies are runs scored / allowed only.
``league_wins`` / ``league_losses`` / ``league_pct`` are never read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

FEATURE_WINDOWS: tuple[int, ...] = (7, 14, 30)

_REQUIRED = ("game_pk", "team_id", "side", "game_date")


def build_team_features(
    team_game_statistics: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build one deterministic pregame feature row per ``(game_pk, team_id)``.

    Expected input columns (Silver ``team_game_statistics`` plus optional
    ``season`` joined from ``silver.games``):

    - ``game_pk``, ``team_id``, ``side`` (``home``/``away``), ``game_date``
    - ``score``, ``is_winner`` — post-game; used only after shift for later rows
    - ``season`` — optional; defaults to calendar year of ``game_date``

    Opponent runs allowed are derived from the paired team row on the same
    ``game_pk``. Rows without a usable prior result do not enter aggregates.
    """
    rows = [_normalize_row(row, index) for index, row in enumerate(team_game_statistics)]
    opponent_score = _opponent_scores(rows)

    by_team: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_team.setdefault(row["team_id"], []).append(row)

    features: list[dict[str, Any]] = []
    for team_id in sorted(by_team):
        team_rows = sorted(
            by_team[team_id],
            key=lambda r: (r["game_date"], r["game_pk"]),
        )
        season_history: dict[str, list[tuple[int, int, bool]]] = {}
        # Chronological completed games: (runs_scored, runs_allowed, won)
        all_history: list[tuple[int, int, bool]] = []

        for row in team_rows:
            season = row["season"]
            prior_season = season_history.setdefault(season, [])
            features.append(
                _feature_row(
                    row,
                    prior_season=prior_season,
                    prior_all=all_history,
                )
            )

            result = _completed_result(row, opponent_score)
            if result is None:
                continue
            prior_season.append(result)
            all_history.append(result)

    features.sort(key=lambda r: (r["game_pk"], r["team_id"]))
    return features


def _normalize_row(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    missing = [name for name in _REQUIRED if name not in row]
    if missing:
        raise ValueError(f"team_game_statistics[{index}] missing required fields {missing}")
    side = row["side"]
    if side not in ("home", "away"):
        raise ValueError(f"team_game_statistics[{index}] side must be 'home' or 'away'")
    game_date = _as_datetime(row["game_date"], index)
    season = row.get("season")
    if season is None:
        season = str(game_date.year)
    else:
        season = str(season)
    return {
        "game_pk": row["game_pk"],
        "team_id": row["team_id"],
        "side": side,
        "game_date": game_date,
        "season": season,
        "score": row.get("score"),
        "is_winner": row.get("is_winner"),
    }


def _as_datetime(value: Any, index: int) -> datetime:
    if isinstance(value, datetime):
        return value
    raise ValueError(
        f"team_game_statistics[{index}] game_date must be datetime, got {type(value)!r}"
    )


def _opponent_scores(rows: Sequence[Mapping[str, Any]]) -> dict[Any, dict[Any, Any]]:
    by_game: dict[Any, dict[Any, Any]] = {}
    for row in rows:
        by_game.setdefault(row["game_pk"], {})[row["team_id"]] = row.get("score")
    return by_game


def _completed_result(
    row: Mapping[str, Any],
    opponent_score: Mapping[Any, Mapping[Any, Any]],
) -> tuple[int, int, bool] | None:
    """Return (runs_scored, runs_allowed, won) when the prior game is usable."""
    score = row["score"]
    is_winner = row["is_winner"]
    if isinstance(score, bool) or not isinstance(score, int):
        return None
    if not isinstance(is_winner, bool):
        return None
    scores = opponent_score.get(row["game_pk"], {})
    allowed = None
    for team_id, other_score in scores.items():
        if team_id != row["team_id"]:
            allowed = other_score
            break
    if isinstance(allowed, bool) or not isinstance(allowed, int):
        return None
    return score, allowed, is_winner


def _feature_row(
    row: Mapping[str, Any],
    *,
    prior_season: Sequence[tuple[int, int, bool]],
    prior_all: Sequence[tuple[int, int, bool]],
) -> dict[str, Any]:
    season_stats = _aggregate(prior_season)
    out: dict[str, Any] = {
        "game_pk": row["game_pk"],
        "team_id": row["team_id"],
        "side": row["side"],
        "is_home": row["side"] == "home",
        "game_date": row["game_date"],
        "season": row["season"],
        "games_played_before": season_stats["games"],
        "wins_before": season_stats["wins"],
        "losses_before": season_stats["losses"],
        "win_pct_before": season_stats["win_pct"],
        "runs_scored_total_before": season_stats["runs_scored_total"],
        "runs_allowed_total_before": season_stats["runs_allowed_total"],
        "run_diff_total_before": season_stats["run_diff_total"],
        "runs_scored_avg_before": season_stats["runs_scored_avg"],
        "runs_allowed_avg_before": season_stats["runs_allowed_avg"],
        "run_diff_avg_before": season_stats["run_diff_avg"],
    }
    for window in FEATURE_WINDOWS:
        window_games = prior_all[-window:] if window else []
        stats = _aggregate(window_games)
        out[f"games_L{window}"] = stats["games"]
        out[f"win_pct_L{window}"] = stats["win_pct"]
        out[f"runs_scored_avg_L{window}"] = stats["runs_scored_avg"]
        out[f"runs_allowed_avg_L{window}"] = stats["runs_allowed_avg"]
        out[f"run_diff_avg_L{window}"] = stats["run_diff_avg"]
        out[f"run_diff_total_L{window}"] = stats["run_diff_total"]
    return out


def _aggregate(games: Sequence[tuple[int, int, bool]]) -> dict[str, Any]:
    n = len(games)
    if n == 0:
        return {
            "games": 0,
            "wins": 0,
            "losses": 0,
            "win_pct": None,
            "runs_scored_total": 0,
            "runs_allowed_total": 0,
            "run_diff_total": 0,
            "runs_scored_avg": None,
            "runs_allowed_avg": None,
            "run_diff_avg": None,
        }
    wins = sum(1 for _, _, won in games if won)
    losses = n - wins
    rs = sum(scored for scored, _, _ in games)
    ra = sum(allowed for _, allowed, _ in games)
    run_diff = rs - ra
    return {
        "games": n,
        "wins": wins,
        "losses": losses,
        "win_pct": wins / n,
        "runs_scored_total": rs,
        "runs_allowed_total": ra,
        "run_diff_total": run_diff,
        "runs_scored_avg": rs / n,
        "runs_allowed_avg": ra / n,
        "run_diff_avg": run_diff / n,
    }
