"""Unit tests for point-in-time starting-pitcher feature construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.starter import STARTER_WINDOWS, build_starter_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _game(game_pk: int, date: str, *, game_type: str = "R", season: str = "2024") -> dict:
    return {
        "game_pk": game_pk,
        "game_date": _dt(date),
        "game_type": game_type,
        "season": season,
    }


def _start(
    game_pk: int,
    team_id: int,
    pitcher_id: int,
    *,
    side: str = "home",
    outs: int | None = 18,
    earned_runs: int | None = 2,
    hits: int | None = 5,
    walks: int | None = 1,
    strikeouts: int | None = 6,
    batters_faced: int | None = 24,
    pitches: int | None = 90,
) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "pitcher_id": pitcher_id,
        "appearance_order": 1,
        "is_actual_starter": True,
        "outs_recorded": outs,
        "earned_runs": earned_runs,
        "hits_allowed": hits,
        "walks": walks,
        "strikeouts": strikeouts,
        "batters_faced": batters_faced,
        "pitches_thrown": pitches,
    }


def _reliever(game_pk: int, team_id: int, pitcher_id: int, order: int = 2) -> dict:
    row = _start(game_pk, team_id, pitcher_id)
    row["appearance_order"] = order
    row["is_actual_starter"] = False
    return row


def _by_key(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["game_pk"], r["team_id"]): r for r in rows}


def test_one_row_per_starter_game_key_sorted() -> None:
    games = [_game(1, "2024-04-01T19:00:00"), _game(2, "2024-04-06T19:00:00")]
    appearances = [
        _start(2, 10, 100),
        _start(1, 10, 100),
        _reliever(1, 10, 999),
    ]
    rows = build_starter_features(appearances, games)
    keys = [(r["game_pk"], r["team_id"]) for r in rows]
    assert keys == [(1, 10), (2, 10)]  # sorted, relievers ignored
    assert all(r["starter_pitcher_id"] == 100 for r in rows)


def test_first_start_is_cold_start() -> None:
    games = [_game(1, "2024-04-01T19:00:00")]
    rows = build_starter_features([_start(1, 10, 100)], games)
    row = rows[0]
    assert row["starter_known"] is True
    assert row["starter_is_probable"] is False
    assert row["season_starts_before"] == 0
    assert row["season_era_before"] is None
    assert row["season_whip_before"] is None
    assert row["season_k_rate_before"] is None
    assert row["season_bb_rate_before"] is None
    assert row["season_k_bb_rate_before"] is None
    assert row["season_ip_per_start_before"] is None
    assert row["days_rest"] is None
    assert row["prev_start_ip"] is None
    assert row["prev_start_pitches"] is None
    assert row["prev_start_batters_faced"] is None
    for window in STARTER_WINDOWS:
        assert row[f"roll_starts_L{window}"] == 0
        assert row[f"roll_era_L{window}"] is None


def test_current_line_excluded_from_own_row() -> None:
    games = [_game(1, "2024-04-01T19:00:00")]
    # Even a catastrophic current line must not appear in the pregame row.
    disaster = _start(1, 10, 100, outs=3, earned_runs=9, hits=12, walks=5, strikeouts=0)
    rows = build_starter_features([disaster], games)
    row = rows[0]
    assert row["season_era_before"] is None  # nothing prior; own line excluded
    assert row["season_starts_before"] == 0


def test_second_start_uses_only_prior_line() -> None:
    games = [_game(1, "2024-04-01T19:00:00"), _game(2, "2024-04-07T19:00:00")]
    appearances = [
        # game 1: 6 IP (18 outs), 2 ER, 5 H, 1 BB, 6 K, 24 BF, 90 pitches
        _start(1, 10, 100),
        # game 2: different line that must NOT influence game 2's own row
        _start(2, 10, 100, outs=21, earned_runs=0, hits=1, walks=0, strikeouts=10,
               batters_faced=22, pitches=95),
    ]
    row = _by_key(build_starter_features(appearances, games))[(2, 10)]
    assert row["season_starts_before"] == 1
    assert row["season_ip_before"] == pytest.approx(6.0)
    assert row["season_era_before"] == pytest.approx(9 * 2 / 6.0)
    assert row["season_whip_before"] == pytest.approx((5 + 1) / 6.0)
    assert row["season_k_rate_before"] == pytest.approx(6 / 24)
    assert row["season_bb_rate_before"] == pytest.approx(1 / 24)
    assert row["season_k_bb_rate_before"] == pytest.approx((6 - 1) / 24)
    assert row["season_ip_per_start_before"] == pytest.approx(6.0)
    # days rest = calendar days between 2024-04-01 and 2024-04-07
    assert row["days_rest"] == 6
    assert row["prev_start_ip"] == pytest.approx(6.0)
    assert row["prev_start_pitches"] == 90
    assert row["prev_start_batters_faced"] == 24
    assert row["roll_starts_L3"] == 1
    assert row["roll_era_L3"] == pytest.approx(9 * 2 / 6.0)


def test_season_aggregates_reset_across_seasons() -> None:
    games = [
        _game(1, "2023-09-01T19:00:00", season="2023"),
        _game(2, "2024-04-05T19:00:00", season="2024"),
    ]
    appearances = [_start(1, 10, 100), _start(2, 10, 100)]
    row = _by_key(build_starter_features(appearances, games))[(2, 10)]
    # New season: season aggregates reset ...
    assert row["season_starts_before"] == 0
    assert row["season_era_before"] is None
    # ... but rolling recent-start and rest carry across seasons.
    assert row["roll_starts_L3"] == 1
    assert row["days_rest"] is not None


def test_rolling_window_limits_to_last_n_starts() -> None:
    games = [_game(pk, f"2024-04-{pk:02d}T19:00:00") for pk in range(1, 7)]
    # Five prior clean starts then a sixth game; L3 must use only starts 3,4,5.
    appearances = []
    for pk in range(1, 6):
        appearances.append(_start(pk, 10, 100, earned_runs=pk, outs=18))
    appearances.append(_start(6, 10, 100))
    row = _by_key(build_starter_features(appearances, games))[(6, 10)]
    assert row["roll_starts_L3"] == 3
    assert row["roll_starts_L5"] == 5
    assert row["roll_starts_L10"] == 5  # only five prior exist
    # L3 ER = starts 3+4+5 = 12 over 18 IP (3*6)
    assert row["roll_era_L3"] == pytest.approx(9 * (3 + 4 + 5) / 18.0)
    assert row["season_starts_before"] == 5


def test_zero_out_prior_start_yields_no_rate() -> None:
    games = [_game(1, "2024-04-01T19:00:00"), _game(2, "2024-04-07T19:00:00")]
    appearances = [
        _start(1, 10, 100, outs=0, earned_runs=0, hits=1, walks=2, strikeouts=0,
               batters_faced=3),
        _start(2, 10, 100),
    ]
    row = _by_key(build_starter_features(appearances, games))[(2, 10)]
    assert row["season_starts_before"] == 1
    assert row["season_ip_before"] == pytest.approx(0.0)
    assert row["season_era_before"] is None  # divide-by-zero innings guarded
    assert row["season_whip_before"] is None
    # K/BB rates use batters faced, which is > 0.
    assert row["season_k_rate_before"] == pytest.approx(0 / 3)
    assert row["season_bb_rate_before"] == pytest.approx(2 / 3)


def test_non_regular_season_games_excluded() -> None:
    games = [
        _game(1, "2024-03-01T19:00:00", game_type="S"),  # spring
        _game(2, "2024-04-05T19:00:00", game_type="R"),
    ]
    appearances = [_start(1, 10, 100), _start(2, 10, 100)]
    rows = build_starter_features(appearances, games)
    keys = [(r["game_pk"], r["team_id"]) for r in rows]
    assert keys == [(2, 10)]  # spring game produced no row ...
    # ... and its line never entered history.
    assert rows[0]["season_starts_before"] == 0
    assert rows[0]["roll_starts_L3"] == 0


def test_incomplete_line_identity_kept_but_not_in_history() -> None:
    games = [_game(1, "2024-04-01T19:00:00"), _game(2, "2024-04-07T19:00:00")]
    appearances = [
        _start(1, 10, 100, batters_faced=None),  # incomplete line
        _start(2, 10, 100),
    ]
    rows = _by_key(build_starter_features(appearances, games))
    # Game 1 still produces a row for the known starter.
    assert rows[(1, 10)]["starter_pitcher_id"] == 100
    assert rows[(1, 10)]["starter_known"] is True
    # Game 2 sees no usable prior line (game 1 line was incomplete).
    assert rows[(2, 10)]["season_starts_before"] == 0
    assert rows[(2, 10)]["days_rest"] is None


def test_duplicate_actual_starter_rejected() -> None:
    games = [_game(1, "2024-04-01T19:00:00")]
    appearances = [_start(1, 10, 100), _start(1, 10, 101)]
    with pytest.raises(ValueError, match="multiple actual-starter"):
        build_starter_features(appearances, games)


def test_duplicate_game_pk_rejected() -> None:
    games = [_game(1, "2024-04-01T19:00:00"), _game(1, "2024-04-02T19:00:00")]
    with pytest.raises(ValueError, match="duplicate game_pk"):
        build_starter_features([_start(1, 10, 100)], games)


def test_missing_game_date_rejected() -> None:
    games = [{"game_pk": 1, "game_type": "R", "game_date": "2024-04-01"}]
    with pytest.raises(ValueError, match="game_date must be datetime"):
        build_starter_features([_start(1, 10, 100)], games)
