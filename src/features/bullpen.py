"""Point-in-time-safe bullpen quality and workload features.

Prediction-time semantics (ADR-002)
-----------------------------------
Each output row is keyed by ``(game_pk, team_id)`` and describes that team's
*bullpen* state **before** the game starts. The "bullpen" is every non-starter
pitcher appearance for the team in a game (``is_actual_starter`` is ``False`` /
``appearance_order > 1``). The current game's own pitching line is a post-game
outcome (``silver.pitcher_appearances`` warning, ADR-002) and is **never** an
input to that game's features: aggregates are computed from prior games only,
then the current game's bullpen line is appended to history
(shift-before-rolling).

Input contract
--------------
``pitcher_appearances`` is a sequence of ``silver.pitcher_appearances`` rows
joined to ``silver.games``. One mapping per pitcher appearance. Required fields:

- ``game_pk``, ``team_id``, ``side`` (``home``/``away``)
- ``game_date`` — ``datetime`` first-pitch timestamp (chronology / windows)
- ``game_type`` — only ``'R'`` (regular season) games are used
- ``is_actual_starter`` (bool) **or** ``appearance_order`` (int) to classify
  starters vs. bullpen

Optional per-appearance pitching-line fields (``None`` treated as ``0`` in
sums): ``outs_recorded``, ``pitches_thrown``, ``earned_runs``,
``hits_allowed``, ``walks``. Optional ``game_number`` orders same-day
doubleheaders.

Ordering and doubleheaders
--------------------------
Within each ``team_id``, games are ordered by ``(game_date, game_number,
game_pk)``. This is deliberately finer than ``official_date`` so a same-day
doubleheader is chronological: the earlier game (game 1 / earlier first pitch)
never sees the nightcap's bullpen usage, while the nightcap *does* count the
opener's usage in its prior-day workload windows.

Windows
-------
- Workload (day-based, rest/fatigue proxy): prior 1 and 3 days. A prior game
  counts when ``current.game_date - prior.game_date <= N days``.
- Quality (game-based): the last 7 / 14 / 30 prior team-games' bullpen lines.

Workload proxy note
-------------------
``outs_recorded`` (and derived innings = outs / 3) is the reliable workload
proxy. ``pitches_thrown`` is nullable in Silver, so ``bullpen_pitches_prior_*``
sums only the non-null values and is best-effort.

Cold start
----------
No prior games: counts are ``0``, innings ``0.0``, ERA/WHIP ``None``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

WORKLOAD_DAY_WINDOWS: tuple[int, ...] = (1, 3)
BULLPEN_GAME_WINDOWS: tuple[int, ...] = (7, 14, 30)

_REQUIRED = ("game_pk", "team_id", "side", "game_date", "game_type")
_LINE_FIELDS = ("outs_recorded", "pitches_thrown", "earned_runs", "hits_allowed", "walks")


def build_bullpen_features(
    pitcher_appearances: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build one deterministic pregame bullpen feature row per ``(game_pk, team_id)``.

    Only regular-season (``game_type == 'R'``) games are considered. Rows are
    returned sorted by ``(game_pk, team_id)``.
    """
    normalized = [_normalize_row(row, index) for index, row in enumerate(pitcher_appearances)]

    # Group appearances into team-games; aggregate only the bullpen portion.
    team_games: dict[tuple[Any, Any], dict[str, Any]] = {}
    for row in normalized:
        if row["game_type"] != "R":
            continue
        key = (row["game_pk"], row["team_id"])
        record = team_games.get(key)
        if record is None:
            record = {
                "game_pk": row["game_pk"],
                "team_id": row["team_id"],
                "side": row["side"],
                "game_date": row["game_date"],
                "game_number": row["game_number"],
                "outs": 0,
                "pitches": 0,
                "earned_runs": 0,
                "hits": 0,
                "walks": 0,
                "appearances": 0,
            }
            team_games[key] = record
        elif record["game_date"] != row["game_date"]:
            raise ValueError(
                f"inconsistent game_date for (game_pk, team_id)={key}: "
                f"{record['game_date']!r} vs {row['game_date']!r}"
            )
        if row["is_bullpen"]:
            record["outs"] += row["outs_recorded"] or 0
            if row["pitches_thrown"] is not None:
                record["pitches"] += row["pitches_thrown"]
            record["earned_runs"] += row["earned_runs"] or 0
            record["hits"] += row["hits_allowed"] or 0
            record["walks"] += row["walks"] or 0
            record["appearances"] += 1

    by_team: dict[Any, list[dict[str, Any]]] = {}
    for record in team_games.values():
        by_team.setdefault(record["team_id"], []).append(record)

    features: list[dict[str, Any]] = []
    for team_id in sorted(by_team):
        games = sorted(
            by_team[team_id],
            key=lambda r: (r["game_date"], r["game_number"], r["game_pk"]),
        )
        # Chronological prior bullpen lines: (game_date, outs, pitches, er, hits, walks, apps)
        history: list[tuple[datetime, int, int, int, int, int, int]] = []
        for record in games:
            features.append(_feature_row(record, history))
            history.append(
                (
                    record["game_date"],
                    record["outs"],
                    record["pitches"],
                    record["earned_runs"],
                    record["hits"],
                    record["walks"],
                    record["appearances"],
                )
            )

    features.sort(key=lambda r: (r["game_pk"], r["team_id"]))
    return features


def _normalize_row(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    missing = [name for name in _REQUIRED if name not in row]
    if missing:
        raise ValueError(f"pitcher_appearances[{index}] missing required fields {missing}")
    side = row["side"]
    if side not in ("home", "away"):
        raise ValueError(f"pitcher_appearances[{index}] side must be 'home' or 'away'")
    if not isinstance(row["game_date"], datetime):
        raise ValueError(
            f"pitcher_appearances[{index}] game_date must be datetime, "
            f"got {type(row['game_date'])!r}"
        )
    game_number = row.get("game_number")
    if game_number is None:
        game_number = 0
    elif isinstance(game_number, bool) or not isinstance(game_number, int):
        raise ValueError(f"pitcher_appearances[{index}] game_number must be int when present")
    normalized: dict[str, Any] = {
        "game_pk": row["game_pk"],
        "team_id": row["team_id"],
        "side": side,
        "game_date": row["game_date"],
        "game_type": row["game_type"],
        "game_number": game_number,
        "is_bullpen": _is_bullpen(row, index),
    }
    for field in _LINE_FIELDS:
        normalized[field] = _optional_int(row.get(field), index, field)
    return normalized


def _is_bullpen(row: Mapping[str, Any], index: int) -> bool:
    starter = row.get("is_actual_starter")
    if starter is not None:
        if not isinstance(starter, bool):
            raise ValueError(f"pitcher_appearances[{index}] is_actual_starter must be bool")
        return not starter
    order = row.get("appearance_order")
    if order is not None:
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"pitcher_appearances[{index}] appearance_order must be int")
        return order > 1
    raise ValueError(
        f"pitcher_appearances[{index}] needs is_actual_starter or appearance_order"
    )


def _optional_int(value: Any, index: int, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"pitcher_appearances[{index}] {field} must be int when present")
    return value


def _feature_row(
    record: Mapping[str, Any],
    history: Sequence[tuple[datetime, int, int, int, int, int, int]],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "game_pk": record["game_pk"],
        "team_id": record["team_id"],
        "side": record["side"],
        "is_home": record["side"] == "home",
        "game_date": record["game_date"],
    }

    current_date = record["game_date"]
    for days in WORKLOAD_DAY_WINDOWS:
        cutoff = timedelta(days=days)
        outs = pitches = appearances = 0
        for prior in reversed(history):
            if current_date - prior[0] > cutoff:
                break
            outs += prior[1]
            pitches += prior[2]
            appearances += prior[6]
        out[f"bullpen_outs_prior_{days}d"] = outs
        out[f"bullpen_ip_prior_{days}d"] = outs / 3
        out[f"bullpen_pitches_prior_{days}d"] = pitches
        out[f"bullpen_appearances_prior_{days}d"] = appearances

    for window in BULLPEN_GAME_WINDOWS:
        recent = history[-window:] if window else []
        games = len(recent)
        outs = sum(rec[1] for rec in recent)
        earned_runs = sum(rec[3] for rec in recent)
        hits = sum(rec[4] for rec in recent)
        walks = sum(rec[5] for rec in recent)
        appearances = sum(rec[6] for rec in recent)
        out[f"bullpen_games_L{window}"] = games
        out[f"bullpen_ip_L{window}"] = outs / 3
        out[f"bullpen_appearances_L{window}"] = appearances
        out[f"bullpen_era_L{window}"] = (27 * earned_runs / outs) if outs else None
        out[f"bullpen_whip_L{window}"] = (3 * (hits + walks) / outs) if outs else None

    return out
