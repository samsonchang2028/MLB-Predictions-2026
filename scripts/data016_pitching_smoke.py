"""DATA-016 live smoke check (opt-in; network; NOT part of the default suite).

Fetches a small set of REAL completed games across MORE THAN ONE season/date
using the repo's production fetcher and the production ``GAME_DETAIL_FIELDS``
projection, then asserts every boxscore pitcher carries a non-empty nested
pitching line and that starters/relievers remain identifiable. This is the gate
the Orchestrator runs BEFORE launching the multi-hour 2021-2025 re-ingest, so a
projection regression can never silently ship 100%-NULL pitching stats again.

This file lives under ``scripts/`` (not ``tests/``), so pytest never collects it
and it is excluded from the default suite.

How to run (from the repository root / worktree)::

    & 'C:\\Users\\sfkim\\OneDrive\\Desktop\\sideproj\\predictions-1\\.venv\\Scripts\\python.exe' scripts/data016_pitching_smoke.py

Exit code 0 = all sampled games returned real pitching lines; non-zero = a
game came back hollow (fail the backfill gate).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.mlb.game_detail import (  # noqa: E402
    ENDPOINT_TEMPLATE,
    GAME_DETAIL_FIELDS,
    _validate_payload,
)
from ingestion.mlb.statsapi_fetchers import (  # noqa: E402
    make_game_detail_fetcher,
    make_schedule_fetcher,
)

# Multiple dates across multiple seasons (2021-2025 development scope).
SAMPLE_DATES = (
    "2021-04-05",
    "2022-06-10",
    "2023-07-15",
    "2024-08-20",
    "2025-04-01",
)
_REQUIRED = (
    "inningsPitched", "outs", "battersFaced",
    "earnedRuns", "hits", "baseOnBalls", "strikeOuts",
)


def _first_final_game_pk(schedule: dict) -> int | None:
    for date in schedule.get("dates", []):
        for game in date.get("games", []):
            if game.get("status", {}).get("abstractGameState") == "Final":
                return int(game["gamePk"])
    return None


def _check_game(detail: dict, game_pk: int) -> None:
    teams = detail["liveData"]["boxscore"]["teams"]
    for side in ("home", "away"):
        side_box = teams[side]
        pitchers = side_box.get("pitchers") or []
        if not pitchers:
            raise AssertionError(f"game {game_pk} {side}: no boxscore pitchers")
        players = side_box["players"]
        for order, pitcher_id in enumerate(pitchers, start=1):
            pitching = players[f"ID{pitcher_id}"]["stats"].get("pitching") or {}
            missing = [key for key in _REQUIRED if pitching.get(key) is None]
            if missing:
                raise AssertionError(
                    f"game {game_pk} {side} pitcher {pitcher_id} missing {missing}"
                )
        # starter vs reliever identity is boxscore appearance order.
        starter = pitchers[0]
        relievers = pitchers[1:]
        print(
            f"    {side}: starter={starter} "
            f"({players[f'ID{starter}']['stats']['pitching']['inningsPitched']} IP), "
            f"{len(relievers)} reliever(s)"
        )


def main() -> int:
    schedule_fetch = make_schedule_fetcher()
    detail_fetch = make_game_detail_fetcher()

    checked = 0
    for date in SAMPLE_DATES:
        schedule = _decode(schedule_fetch({"sportId": 1, "date": date}))
        game_pk = _first_final_game_pk(schedule)
        if game_pk is None:
            print(f"[skip] {date}: no completed game")
            continue
        endpoint = ENDPOINT_TEMPLATE.format(game_pk=game_pk)
        payload = detail_fetch(endpoint, {"fields": GAME_DETAIL_FIELDS})
        if payload is None:
            raise AssertionError(f"{date} game {game_pk}: upstream returned no payload")
        # Exercises the production hollow-payload guard against the real API.
        text = _validate_payload(payload, game_pk)
        print(f"[ok]   {date} game {game_pk}: guard passed ({len(text)} chars)")
        _check_game(_decode(payload), game_pk)
        checked += 1

    if checked < 2:
        raise AssertionError(
            f"smoke check inspected only {checked} game(s) across <2 dates"
        )
    print(f"\nPASS: {checked} completed games across {checked} dates carry real pitching lines.")
    return 0


def _decode(payload: bytes) -> dict:
    import json

    return json.loads(payload.decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
