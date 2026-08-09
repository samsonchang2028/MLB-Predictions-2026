"""Unit tests for point-in-time bullpen feature construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.bullpen import (
    BULLPEN_GAME_WINDOWS,
    WORKLOAD_DAY_WINDOWS,
    build_bullpen_features,
)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _appearance(
    game_pk: int,
    team_id: int,
    game_date: str,
    *,
    order: int,
    outs: int | None = None,
    pitches: int | None = None,
    earned_runs: int | None = None,
    hits: int | None = None,
    walks: int | None = None,
    side: str = "home",
    game_type: str = "R",
    game_number: int = 1,
) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "game_date": _dt(game_date),
        "game_type": game_type,
        "appearance_order": order,
        "is_actual_starter": order == 1,
        "outs_recorded": outs,
        "pitches_thrown": pitches,
        "earned_runs": earned_runs,
        "hits_allowed": hits,
        "walks": walks,
        "game_number": game_number,
    }


def _team_game(
    game_pk: int,
    team_id: int,
    game_date: str,
    *,
    starter_outs: int = 15,
    bullpen: list[dict] | None = None,
    side: str = "home",
    game_type: str = "R",
    game_number: int = 1,
) -> list[dict]:
    """One starter (order 1) plus a list of bullpen line dicts (order >= 2)."""
    rows = [
        _appearance(
            game_pk,
            team_id,
            game_date,
            order=1,
            outs=starter_outs,
            side=side,
            game_type=game_type,
            game_number=game_number,
        )
    ]
    for index, line in enumerate(bullpen or [], start=2):
        rows.append(
            _appearance(
                game_pk,
                team_id,
                game_date,
                order=index,
                side=side,
                game_type=game_type,
                game_number=game_number,
                **line,
            )
        )
    return rows


def _by_key(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["game_pk"], r["team_id"]): r for r in rows}


def test_one_deterministic_row_per_team_game_and_order_independent() -> None:
    rows = _team_game(
        1, 10, "2024-04-01T19:00:00", bullpen=[{"outs": 3, "earned_runs": 1}]
    ) + _team_game(
        2, 10, "2024-04-02T19:00:00", bullpen=[{"outs": 6, "earned_runs": 0}]
    )
    forward = build_bullpen_features(rows)
    shuffled = build_bullpen_features(list(reversed(rows)))

    assert forward == shuffled
    assert [(r["game_pk"], r["team_id"]) for r in forward] == [(1, 10), (2, 10)]


def test_first_game_cold_start_is_zero_counts_and_null_rates() -> None:
    rows = _team_game(1, 10, "2024-04-01T19:00:00", bullpen=[{"outs": 3, "earned_runs": 1}])
    row = _by_key(build_bullpen_features(rows))[(1, 10)]

    for days in WORKLOAD_DAY_WINDOWS:
        assert row[f"bullpen_outs_prior_{days}d"] == 0
        assert row[f"bullpen_ip_prior_{days}d"] == 0.0
        assert row[f"bullpen_pitches_prior_{days}d"] == 0
        assert row[f"bullpen_appearances_prior_{days}d"] == 0
    for window in BULLPEN_GAME_WINDOWS:
        assert row[f"bullpen_games_L{window}"] == 0
        assert row[f"bullpen_ip_L{window}"] == 0.0
        assert row[f"bullpen_appearances_L{window}"] == 0
        assert row[f"bullpen_era_L{window}"] is None
        assert row[f"bullpen_whip_L{window}"] is None


def test_current_game_bullpen_line_never_enters_its_own_features() -> None:
    # A huge current-game bullpen line must not appear in that game's features.
    rows = _team_game(
        1,
        10,
        "2024-04-01T19:00:00",
        bullpen=[{"outs": 30, "pitches": 99, "earned_runs": 9, "hits": 12, "walks": 5}],
    )
    row = _by_key(build_bullpen_features(rows))[(1, 10)]
    assert row["bullpen_outs_prior_1d"] == 0
    assert row["bullpen_ip_L7"] == 0.0
    assert row["bullpen_era_L7"] is None


def test_prior_day_workload_windows_are_deterministic() -> None:
    # Team 10 plays on 4/01, 4/02, 4/04. Feature row for 4/04 sees:
    #   prior 1 day -> only 4/04-1day .. i.e. nothing on 4/03, so empty.
    #   prior 3 days -> 4/02 (2 days) and 4/04 excludes current; 4/01 is 3 days.
    rows = (
        _team_game(1, 10, "2024-04-01T19:00:00", bullpen=[{"outs": 3, "pitches": 20}])
        + _team_game(2, 10, "2024-04-02T19:00:00", bullpen=[{"outs": 6, "pitches": 40}])
        + _team_game(3, 10, "2024-04-04T19:00:00", bullpen=[{"outs": 9, "pitches": 60}])
    )
    features = _by_key(build_bullpen_features(rows))

    g2 = features[(2, 10)]
    # 4/02 game: prior 1 day includes 4/01 (exactly 1 day earlier).
    assert g2["bullpen_outs_prior_1d"] == 3
    assert g2["bullpen_ip_prior_1d"] == 1.0
    assert g2["bullpen_pitches_prior_1d"] == 20
    assert g2["bullpen_appearances_prior_1d"] == 1
    assert g2["bullpen_outs_prior_3d"] == 3

    g3 = features[(3, 10)]
    # 4/04 game: 4/03 empty -> prior 1 day is 0. Prior 3 days includes 4/02
    # (2 days back) and 4/01 (exactly 3 days back): outs 6 + 3, pitches 40 + 20.
    assert g3["bullpen_outs_prior_1d"] == 0
    assert g3["bullpen_appearances_prior_1d"] == 0
    assert g3["bullpen_outs_prior_3d"] == 9
    assert g3["bullpen_pitches_prior_3d"] == 60
    assert g3["bullpen_appearances_prior_3d"] == 2


def test_game_window_era_and_whip_from_prior_games_only() -> None:
    # Two prior bullpen games then a third. Third game's L7 aggregates the two priors.
    rows = (
        _team_game(
            1, 10, "2024-04-01T19:00:00",
            bullpen=[{"outs": 3, "earned_runs": 1, "hits": 2, "walks": 1}],
        )
        + _team_game(
            2, 10, "2024-04-02T19:00:00",
            bullpen=[{"outs": 6, "earned_runs": 2, "hits": 4, "walks": 2}],
        )
        + _team_game(
            3, 10, "2024-04-03T19:00:00",
            bullpen=[{"outs": 99, "earned_runs": 99, "hits": 99, "walks": 99}],
        )
    )
    g3 = _by_key(build_bullpen_features(rows))[(3, 10)]
    # priors: outs 9, ER 3, hits 6, walks 3.
    assert g3["bullpen_games_L7"] == 2
    assert g3["bullpen_ip_L7"] == 3.0
    assert g3["bullpen_era_L7"] == pytest.approx(27 * 3 / 9)
    assert g3["bullpen_whip_L7"] == pytest.approx(3 * (6 + 3) / 9)
    assert g3["bullpen_appearances_L7"] == 2


def test_game_window_respects_window_length() -> None:
    smallest = min(BULLPEN_GAME_WINDOWS)
    rows: list[dict] = []
    day = 1
    # Build (smallest + 2) prior games each with 3 bullpen outs, then a target game.
    for pk in range(1, smallest + 3):
        rows += _team_game(
            pk, 10, f"2024-06-{day:02d}T19:00:00", bullpen=[{"outs": 3, "earned_runs": 1}]
        )
        day += 1
    rows += _team_game(999, 10, f"2024-06-{day:02d}T19:00:00", bullpen=[{"outs": 3}])

    target = _by_key(build_bullpen_features(rows))[(999, 10)]
    # Only the last `smallest` prior games count in the smallest window.
    assert target[f"bullpen_games_L{smallest}"] == smallest
    assert target[f"bullpen_ip_L{smallest}"] == smallest * 3 / 3


def test_starter_line_is_excluded_from_bullpen_aggregates() -> None:
    # A complete game (starter only, no bullpen) contributes zero bullpen workload.
    rows = _team_game(
        1, 10, "2024-04-01T19:00:00", starter_outs=27, bullpen=[]
    ) + _team_game(
        2, 10, "2024-04-02T19:00:00", starter_outs=15, bullpen=[{"outs": 3, "earned_runs": 0}]
    )
    g2 = _by_key(build_bullpen_features(rows))[(2, 10)]
    # Prior day (4/01) had NO bullpen usage despite a big starter line.
    assert g2["bullpen_outs_prior_1d"] == 0
    assert g2["bullpen_appearances_prior_1d"] == 0
    assert g2["bullpen_games_L7"] == 1  # game exists, but bullpen ip is 0
    assert g2["bullpen_ip_L7"] == 0.0
    assert g2["bullpen_era_L7"] is None


def test_non_regular_season_games_are_excluded() -> None:
    rows = _team_game(
        1, 10, "2024-03-01T19:00:00", bullpen=[{"outs": 30, "earned_runs": 9}],
        game_type="S",  # spring training
    ) + _team_game(
        2, 10, "2024-04-02T19:00:00", bullpen=[{"outs": 3, "earned_runs": 0}],
        game_type="R",
    )
    features = build_bullpen_features(rows)
    keys = {(r["game_pk"], r["team_id"]) for r in features}
    assert keys == {(2, 10)}  # spring game not emitted
    g2 = _by_key(features)[(2, 10)]
    # Spring bullpen usage must not leak into the regular-season workload window.
    assert g2["bullpen_outs_prior_3d"] == 0
    assert g2["bullpen_games_L7"] == 0


def test_teams_are_isolated() -> None:
    rows = (
        _team_game(1, 10, "2024-04-01T19:00:00", bullpen=[{"outs": 3}])
        + _team_game(1, 20, "2024-04-01T19:00:00", bullpen=[{"outs": 9}], side="away")
        + _team_game(2, 10, "2024-04-02T19:00:00", bullpen=[{"outs": 3}])
    )
    g2_10 = _by_key(build_bullpen_features(rows))[(2, 10)]
    # Team 10 only sees its own 3 outs from 4/01, not team 20's 9 outs.
    assert g2_10["bullpen_outs_prior_1d"] == 3


def test_none_pitches_are_skipped_but_outs_still_count() -> None:
    rows = _team_game(
        1, 10, "2024-04-01T19:00:00", bullpen=[{"outs": 6, "pitches": None}]
    ) + _team_game(
        2, 10, "2024-04-02T19:00:00", bullpen=[{"outs": 3}]
    )
    g2 = _by_key(build_bullpen_features(rows))[(2, 10)]
    assert g2["bullpen_outs_prior_1d"] == 6
    assert g2["bullpen_pitches_prior_1d"] == 0  # pitches unavailable, best-effort


def test_missing_required_field_raises() -> None:
    with pytest.raises(ValueError):
        build_bullpen_features([{"game_pk": 1, "team_id": 10, "side": "home"}])


def test_classifier_requires_starter_or_order() -> None:
    row = {
        "game_pk": 1,
        "team_id": 10,
        "side": "home",
        "game_date": _dt("2024-04-01T19:00:00"),
        "game_type": "R",
    }
    with pytest.raises(ValueError):
        build_bullpen_features([row])
