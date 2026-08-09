"""Point-in-time-safe starting-pitcher features.

Prediction-time semantics (ADR-002)
-----------------------------------
Each output row is keyed by ``(game_pk, team_id)`` and describes that team's
starting pitcher's state *before* first pitch. The starter's own current-game
pitching line (``silver.pitcher_appearances`` fields ``innings_pitched``
through ``home_runs_allowed``, and the derived ``outs_recorded`` /
``batters_faced`` counts) is a post-game outcome fact and is **never** used as
an input to that same game's feature row. It contributes only to *later* rows
for the same pitcher, after an explicit shift (shift-before-rolling).

Ordering: within each ``pitcher_id`` the pitcher's starts are ordered by
``(game_date, game_pk)``. Season aggregates use only that pitcher's prior
regular-season starts in the same ``season``; rolling windows use the pitcher's
last ``N`` prior regular-season starts (across seasons). Future starts can never
influence an earlier row because only strictly-prior starts are read.

Scope
-----
Only regular-season games (``silver.games.game_type == 'R'``) produce rows and
enter history. Spring / exhibition / postseason games are excluded so their
pitching lines never leak into regular-season features.

Starter identity (missing / changed starters)
----------------------------------------------
The *actual* starter is the certified boxscore identity, resolved per
``(game_pk, team_id)`` in this priority order:

1. the ``silver.pitcher_appearances`` row with ``is_actual_starter`` — the
   pitcher who actually took the mound, together with the (excluded) line;
2. else ``silver.pitcher_starters.actual_pitcher_id`` — identity known but no
   boxscore line was recorded for this game;
3. else ``silver.pitcher_starters.probable_pitcher_id`` — falls back to the
   pregame probable and flags ``starter_is_probable = True``;
4. else the starter is unknown: ``starter_pitcher_id = None``,
   ``starter_known = False``, and every metric is ``None`` / ``0``.

The recorded actual starter may differ from the probable; this builder always
uses the certified actual identity when it exists and never silently attributes
a game to the probable pitcher when the actual is known. A pitcher's history is
built only from starts that actually carry a usable line, so identity-only rows
(paths 2-4) summarise prior starts without inventing a current-game line.

Cold start / window edges
-------------------------
- First recorded start for a pitcher (no prior starts): all rate metrics are
  ``None``, ``days_rest`` and previous-start workload are ``None``, and count
  fields (``season_starts_before``, ``roll_starts_L{N}``) are ``0``.
- Season aggregates reset per ``season``; a pitcher's season opener has
  ``season_starts_before == 0`` even when prior seasons exist.
- ``days_rest`` and previous-start workload use the most recent prior
  regular-season start across seasons (whole calendar days).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

# Recent-start rolling windows, measured in number of prior starts.
STARTER_WINDOWS: tuple[int, ...] = (3, 5, 10)

_APPEARANCE_REQUIRED = (
    "game_pk",
    "team_id",
    "side",
    "pitcher_id",
    "is_actual_starter",
)
_GAME_REQUIRED = ("game_pk", "game_date")

# Line fields required for a start to contribute to rate aggregates. ``pitches``
# is intentionally excluded: it is used only for previous-start workload and may
# be legitimately absent in older boxscores.
_LINE_REQUIRED = (
    "outs_recorded",
    "earned_runs",
    "hits_allowed",
    "walks",
    "strikeouts",
    "batters_faced",
)


def build_starter_features(
    pitcher_appearances: Sequence[Mapping[str, Any]],
    games: Sequence[Mapping[str, Any]],
    pitcher_starters: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build one deterministic pregame row per ``(game_pk, team_id)`` starter.

    Parameters
    ----------
    pitcher_appearances:
        Rows from ``silver.pitcher_appearances``. Only ``is_actual_starter``
        rows are used, both for identity and for historical lines; reliever
        rows are ignored. Required keys: ``game_pk``, ``team_id``, ``side``,
        ``pitcher_id``, ``is_actual_starter``. Line keys (``outs_recorded``,
        ``earned_runs``, ``hits_allowed``, ``walks``, ``strikeouts``,
        ``batters_faced``, optional ``pitches_thrown``) enter history only when
        all required line fields are present ints.
    games:
        Rows from ``silver.games``. Required keys: ``game_pk``, ``game_date``
        (``datetime``). Optional ``game_type`` (only ``'R'`` games are kept;
        when omitted the game is treated as regular season) and ``season``
        (defaults to the calendar year of ``game_date``).
    pitcher_starters:
        Optional rows from ``silver.pitcher_starters`` used to resolve the
        starter identity when no boxscore start line exists. Keys: ``game_pk``,
        ``team_id``, ``side``, ``actual_pitcher_id``, ``probable_pitcher_id``.
    """
    game_index = _index_games(games)
    starter_index = _index_starters(pitcher_starters or [])
    appearance_starts = _appearance_starts(pitcher_appearances, game_index)

    events = _build_events(appearance_starts, starter_index, game_index)

    by_pitcher: dict[Any, list[dict[str, Any]]] = {}
    unattached: list[dict[str, Any]] = []
    for event in events:
        if event["starter_pitcher_id"] is None:
            unattached.append(event)
        else:
            by_pitcher.setdefault(event["starter_pitcher_id"], []).append(event)

    features: list[dict[str, Any]] = []
    for pitcher_id in sorted(by_pitcher, key=_sort_key):
        pitcher_events = sorted(
            by_pitcher[pitcher_id],
            key=lambda e: (e["game_date"], e["game_pk"]),
        )
        prior_all: list[dict[str, Any]] = []
        for event in pitcher_events:
            prior_season = [s for s in prior_all if s["season"] == event["season"]]
            features.append(_feature_row(event, prior_all, prior_season))
            if event["line"] is not None:
                prior_all.append(event["line"])

    for event in unattached:
        features.append(_feature_row(event, [], []))

    features.sort(key=lambda r: (r["game_pk"], r["team_id"]))
    return features


def _sort_key(value: Any) -> tuple[int, Any]:
    """Total order over possibly-mixed pitcher id types for determinism."""
    return (0, value) if isinstance(value, (int, float)) else (1, str(value))


def _index_games(games: Sequence[Mapping[str, Any]]) -> dict[Any, dict[str, Any]]:
    index: dict[Any, dict[str, Any]] = {}
    for position, row in enumerate(games):
        missing = [name for name in _GAME_REQUIRED if name not in row]
        if missing:
            raise ValueError(f"games[{position}] missing required fields {missing}")
        game_pk = row["game_pk"]
        if game_pk in index:
            raise ValueError(f"duplicate game_pk={game_pk} in games")
        game_type = row.get("game_type")
        if game_type is not None and game_type != "R":
            continue  # exclude spring / exhibition / postseason
        game_date = _as_datetime(row["game_date"], position)
        season = row.get("season")
        season = str(game_date.year) if season is None else str(season)
        index[game_pk] = {"game_date": game_date, "season": season}
    return index


def _index_starters(
    pitcher_starters: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, Any], dict[str, Any]]:
    index: dict[tuple[Any, Any], dict[str, Any]] = {}
    for position, row in enumerate(pitcher_starters):
        for name in ("game_pk", "team_id"):
            if name not in row:
                raise ValueError(f"pitcher_starters[{position}] missing field {name!r}")
        key = (row["game_pk"], row["team_id"])
        if key in index:
            raise ValueError(f"duplicate (game_pk, team_id)={key} in pitcher_starters")
        index[key] = {
            "side": row.get("side"),
            "actual_pitcher_id": row.get("actual_pitcher_id"),
            "probable_pitcher_id": row.get("probable_pitcher_id"),
        }
    return index


def _appearance_starts(
    pitcher_appearances: Sequence[Mapping[str, Any]],
    game_index: Mapping[Any, Mapping[str, Any]],
) -> dict[tuple[Any, Any], dict[str, Any]]:
    """Map (game_pk, team_id) -> actual-starter appearance for regular games."""
    starts: dict[tuple[Any, Any], dict[str, Any]] = {}
    for position, row in enumerate(pitcher_appearances):
        missing = [name for name in _APPEARANCE_REQUIRED if name not in row]
        if missing:
            raise ValueError(
                f"pitcher_appearances[{position}] missing required fields {missing}"
            )
        if not row["is_actual_starter"]:
            continue
        game_pk = row["game_pk"]
        game = game_index.get(game_pk)
        if game is None:
            continue  # not a regular-season game (or absent from games)
        key = (game_pk, row["team_id"])
        if key in starts:
            raise ValueError(
                f"multiple actual-starter appearances for (game_pk, team_id)={key}"
            )
        starts[key] = {
            "game_pk": game_pk,
            "team_id": row["team_id"],
            "side": row["side"],
            "pitcher_id": row["pitcher_id"],
            "game_date": game["game_date"],
            "season": game["season"],
            "line": _start_line(row, game),
        }
    return starts


def _build_events(
    appearance_starts: Mapping[tuple[Any, Any], Mapping[str, Any]],
    starter_index: Mapping[tuple[Any, Any], Mapping[str, Any]],
    game_index: Mapping[Any, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One event per regular-season (game_pk, team_id) with a resolved starter."""
    events: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    for key, start in appearance_starts.items():
        seen.add(key)
        events.append(
            {
                "game_pk": start["game_pk"],
                "team_id": start["team_id"],
                "side": start["side"],
                "game_date": start["game_date"],
                "season": start["season"],
                "starter_pitcher_id": start["pitcher_id"],
                "starter_known": True,
                "starter_is_probable": False,
                "line": start["line"],
            }
        )

    for key, starter in starter_index.items():
        if key in seen:
            continue  # boxscore actual starter already resolved this game/team
        game_pk, team_id = key
        game = game_index.get(game_pk)
        if game is None:
            continue  # not a regular-season game
        actual = starter["actual_pitcher_id"]
        probable = starter["probable_pitcher_id"]
        if actual is not None:
            pitcher_id, is_probable, known = actual, False, True
        elif probable is not None:
            pitcher_id, is_probable, known = probable, True, True
        else:
            pitcher_id, is_probable, known = None, False, False
        events.append(
            {
                "game_pk": game_pk,
                "team_id": team_id,
                "side": starter["side"],
                "game_date": game["game_date"],
                "season": game["season"],
                "starter_pitcher_id": pitcher_id,
                "starter_known": known,
                "starter_is_probable": is_probable,
                "line": None,  # no recorded boxscore line for this game
            }
        )

    return events


def _start_line(
    row: Mapping[str, Any], game: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return a usable historical start line, or ``None`` if incomplete.

    All required counting fields must be present ints (``bool`` rejected). This
    line is only ever appended to a pitcher's *prior* history; it is never read
    for its own game's feature row.
    """
    values: dict[str, Any] = {"season": game["season"]}
    for field in _LINE_REQUIRED:
        value = row.get(field)
        if not _is_int(value):
            return None
        values[field] = value
    pitches = row.get("pitches_thrown")
    values["pitches_thrown"] = pitches if _is_int(pitches) else None
    values["game_date"] = game["game_date"]
    return values


def _feature_row(
    event: Mapping[str, Any],
    prior_all: Sequence[Mapping[str, Any]],
    prior_season: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    season_stats = _aggregate(prior_season)
    out: dict[str, Any] = {
        "game_pk": event["game_pk"],
        "team_id": event["team_id"],
        "side": event["side"],
        "is_home": event["side"] == "home" if event["side"] is not None else None,
        "game_date": event["game_date"],
        "season": event["season"],
        "starter_pitcher_id": event["starter_pitcher_id"],
        "starter_known": event["starter_known"],
        "starter_is_probable": event["starter_is_probable"],
        "season_starts_before": season_stats["starts"],
        "season_ip_before": season_stats["ip"],
        "season_era_before": season_stats["era"],
        "season_whip_before": season_stats["whip"],
        "season_k_rate_before": season_stats["k_rate"],
        "season_bb_rate_before": season_stats["bb_rate"],
        "season_k_bb_rate_before": season_stats["k_bb_rate"],
        "season_ip_per_start_before": season_stats["ip_per_start"],
    }

    if prior_all:
        last = prior_all[-1]
        out["days_rest"] = (
            event["game_date"].date() - last["game_date"].date()
        ).days
        out["prev_start_ip"] = last["outs_recorded"] / 3
        out["prev_start_pitches"] = last["pitches_thrown"]
        out["prev_start_batters_faced"] = last["batters_faced"]
    else:
        out["days_rest"] = None
        out["prev_start_ip"] = None
        out["prev_start_pitches"] = None
        out["prev_start_batters_faced"] = None

    for window in STARTER_WINDOWS:
        stats = _aggregate(prior_all[-window:])
        out[f"roll_starts_L{window}"] = stats["starts"]
        out[f"roll_era_L{window}"] = stats["era"]
        out[f"roll_whip_L{window}"] = stats["whip"]
        out[f"roll_k_rate_L{window}"] = stats["k_rate"]
        out[f"roll_ip_per_start_L{window}"] = stats["ip_per_start"]

    return out


def _aggregate(starts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(starts)
    if n == 0:
        return {
            "starts": 0,
            "ip": None,
            "era": None,
            "whip": None,
            "k_rate": None,
            "bb_rate": None,
            "k_bb_rate": None,
            "ip_per_start": None,
        }
    outs = sum(s["outs_recorded"] for s in starts)
    ip = outs / 3
    earned = sum(s["earned_runs"] for s in starts)
    hits = sum(s["hits_allowed"] for s in starts)
    walks = sum(s["walks"] for s in starts)
    strikeouts = sum(s["strikeouts"] for s in starts)
    faced = sum(s["batters_faced"] for s in starts)
    return {
        "starts": n,
        "ip": ip,
        "era": 9 * earned / ip if ip > 0 else None,
        "whip": (hits + walks) / ip if ip > 0 else None,
        "k_rate": strikeouts / faced if faced > 0 else None,
        "bb_rate": walks / faced if faced > 0 else None,
        "k_bb_rate": (strikeouts - walks) / faced if faced > 0 else None,
        "ip_per_start": ip / n,
    }


def _as_datetime(value: Any, position: int) -> datetime:
    if isinstance(value, datetime):
        return value
    raise ValueError(
        f"games[{position}] game_date must be datetime, got {type(value)!r}"
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
