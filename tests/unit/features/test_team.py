"""Unit tests for point-in-time team feature construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.team import FEATURE_WINDOWS, build_team_features


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _game_pair(
    game_pk: int,
    game_date: str,
    *,
    home_id: int,
    away_id: int,
    home_score: int | None,
    away_score: int | None,
    home_won: bool | None,
    away_won: bool | None,
    season: str = "2024",
) -> list[dict]:
    when = _dt(game_date)
    return [
        {
            "game_pk": game_pk,
            "team_id": home_id,
            "side": "home",
            "game_date": when,
            "season": season,
            "score": home_score,
            "is_winner": home_won,
        },
        {
            "game_pk": game_pk,
            "team_id": away_id,
            "side": "away",
            "game_date": when,
            "season": season,
            "score": away_score,
            "is_winner": away_won,
        },
    ]


def _by_key(rows: list[dict]) -> dict[tuple[int, int], dict]:
    return {(r["game_pk"], r["team_id"]): r for r in rows}


def test_one_deterministic_row_per_team_game_key() -> None:
    rows = (
        _game_pair(
            1,
            "2024-04-01T19:00:00",
            home_id=10,
            away_id=20,
            home_score=5,
            away_score=3,
            home_won=True,
            away_won=False,
        )
        + _game_pair(
            2,
            "2024-04-02T19:00:00",
            home_id=20,
            away_id=10,
            home_score=1,
            away_score=4,
            home_won=False,
            away_won=True,
        )
    )
    # Non-chronological input order must not change output identity or values.
    shuffled = list(reversed(rows))
    a = build_team_features(rows)
    b = build_team_features(shuffled)

    assert len(a) == 4
    assert [(r["game_pk"], r["team_id"]) for r in a] == sorted(
        (r["game_pk"], r["team_id"]) for r in a
    )
    assert a == b
    keys = {(r["game_pk"], r["team_id"]) for r in a}
    assert keys == {(1, 10), (1, 20), (2, 10), (2, 20)}


def test_first_game_cold_start_is_null_rates_and_zero_counts() -> None:
    rows = _game_pair(
        1,
        "2024-04-01T19:00:00",
        home_id=10,
        away_id=20,
        home_score=5,
        away_score=3,
        home_won=True,
        away_won=False,
    )
    features = _by_key(build_team_features(rows))
    home = features[(1, 10)]

    assert home["games_played_before"] == 0
    assert home["wins_before"] == 0
    assert home["losses_before"] == 0
    assert home["win_pct_before"] is None
    assert home["runs_scored_total_before"] == 0
    assert home["runs_allowed_total_before"] == 0
    assert home["run_diff_total_before"] == 0
    assert home["runs_scored_avg_before"] is None
    assert home["runs_allowed_avg_before"] is None
    assert home["run_diff_avg_before"] is None
    for window in FEATURE_WINDOWS:
        assert home[f"games_L{window}"] == 0
        assert home[f"win_pct_L{window}"] is None
        assert home[f"runs_scored_avg_L{window}"] is None
        assert home[f"runs_allowed_avg_L{window}"] is None
        assert home[f"run_diff_avg_L{window}"] is None
        assert home[f"run_diff_total_L{window}"] == 0


def test_shift_excludes_current_game_from_its_own_features() -> None:
    rows = (
        _game_pair(
            1,
            "2024-04-01T19:00:00",
            home_id=10,
            away_id=20,
            home_score=5,
            away_score=3,
            home_won=True,
            away_won=False,
        )
        + _game_pair(
            2,
            "2024-04-02T19:00:00",
            home_id=10,
            away_id=30,
            home_score=2,
            away_score=8,
            home_won=False,
            away_won=True,
        )
    )
    features = _by_key(build_team_features(rows))
    g1 = features[(1, 10)]
    g2 = features[(2, 10)]

    # Game 1 cold start; game 1's 5-3 win must not appear in game 1 features.
    assert g1["games_played_before"] == 0
    assert g1["win_pct_before"] is None

    # Game 2 sees only game 1.
    assert g2["games_played_before"] == 1
    assert g2["wins_before"] == 1
    assert g2["win_pct_before"] == 1.0
    assert g2["runs_scored_total_before"] == 5
    assert g2["runs_allowed_total_before"] == 3
    assert g2["run_diff_total_before"] == 2
    assert g2["runs_scored_avg_before"] == 5.0
    assert g2["runs_allowed_avg_before"] == 3.0
    assert g2["run_diff_avg_before"] == 2.0
    # Current game 2 result (2-8 loss) must not affect game 2 features.
    assert g2["win_pct_before"] != 0.5


def test_season_aggregates_reset_across_seasons() -> None:
    rows = (
        _game_pair(
            1,
            "2024-09-30T19:00:00",
            home_id=10,
            away_id=20,
            home_score=4,
            away_score=1,
            home_won=True,
            away_won=False,
            season="2024",
        )
        + _game_pair(
            2,
            "2025-04-01T19:00:00",
            home_id=10,
            away_id=20,
            home_score=3,
            away_score=2,
            home_won=True,
            away_won=False,
            season="2025",
        )
    )
    features = _by_key(build_team_features(rows))
    opener = features[(2, 10)]

    assert opener["games_played_before"] == 0
    assert opener["win_pct_before"] is None
    # Recent-form windows may still include prior-season completed games.
    assert opener["games_L7"] == 1
    assert opener["win_pct_L7"] == 1.0
    assert opener["runs_scored_avg_L7"] == 4.0


def test_partial_rolling_windows_use_available_prior_games() -> None:
    rows: list[dict] = []
    # Team 10 plays three completed games against rotating opponents.
    schedule = [
        (1, "2024-04-01T19:00:00", 20, 5, 2, True),
        (2, "2024-04-02T19:00:00", 30, 1, 4, False),
        (3, "2024-04-03T19:00:00", 40, 7, 0, True),
        (4, "2024-04-04T19:00:00", 50, None, None, None),  # pregame / incomplete
    ]
    for game_pk, when, opp, hs, as_, won in schedule:
        rows.extend(
            _game_pair(
                game_pk,
                when,
                home_id=10,
                away_id=opp,
                home_score=hs,
                away_score=as_,
                home_won=won,
                away_won=(None if won is None else (not won)),
            )
        )

    features = _by_key(build_team_features(rows))
    fourth = features[(4, 10)]

    assert fourth["games_played_before"] == 3
    assert fourth["wins_before"] == 2
    assert fourth["win_pct_before"] == pytest.approx(2 / 3)
    assert fourth["runs_scored_total_before"] == 13
    assert fourth["runs_allowed_total_before"] == 6
    assert fourth["run_diff_total_before"] == 7

    # Fewer than 7/14/30 priors → partial window equal to all 3 completed.
    for window in FEATURE_WINDOWS:
        assert fourth[f"games_L{window}"] == 3
        assert fourth[f"win_pct_L{window}"] == pytest.approx(2 / 3)
        assert fourth[f"runs_scored_avg_L{window}"] == pytest.approx(13 / 3)
        assert fourth[f"runs_allowed_avg_L{window}"] == pytest.approx(6 / 3)
        assert fourth[f"run_diff_avg_L{window}"] == pytest.approx(7 / 3)
        assert fourth[f"run_diff_total_L{window}"] == 7


def test_rolling_window_uses_most_recent_n_only() -> None:
    rows: list[dict] = []
    # Eight completed games for team 10; L7 for game 9 should drop the first.
    results = [True, True, True, True, True, True, True, False]
    for i, won in enumerate(results, start=1):
        hs, as_ = (3, 1) if won else (1, 3)
        rows.extend(
            _game_pair(
                i,
                f"2024-04-{i:02d}T19:00:00",
                home_id=10,
                away_id=100 + i,
                home_score=hs,
                away_score=as_,
                home_won=won,
                away_won=not won,
            )
        )
    rows.extend(
        _game_pair(
            9,
            "2024-04-09T19:00:00",
            home_id=10,
            away_id=200,
            home_score=None,
            away_score=None,
            home_won=None,
            away_won=None,
        )
    )

    ninth = _by_key(build_team_features(rows))[(9, 10)]
    assert ninth["games_played_before"] == 8
    assert ninth["win_pct_before"] == pytest.approx(7 / 8)
    assert ninth["games_L7"] == 7
    # Last 7 of the 8: six wins then a loss → 6/7.
    assert ninth["win_pct_L7"] == pytest.approx(6 / 7)
    assert ninth["games_L14"] == 8
    assert ninth["win_pct_L14"] == pytest.approx(7 / 8)


def test_home_away_orientation_is_preserved() -> None:
    rows = _game_pair(
        1,
        "2024-04-01T19:00:00",
        home_id=10,
        away_id=20,
        home_score=2,
        away_score=1,
        home_won=True,
        away_won=False,
    )
    features = _by_key(build_team_features(rows))
    assert features[(1, 10)]["side"] == "home"
    assert features[(1, 10)]["is_home"] is True
    assert features[(1, 20)]["side"] == "away"
    assert features[(1, 20)]["is_home"] is False


def test_doubleheader_order_is_game_date_then_game_pk() -> None:
    # Same calendar day, later game_pk / later tip — first game feeds second.
    early = _dt("2024-04-01T13:05:00")
    late = _dt("2024-04-01T19:05:00")
    rows = [
        {
            "game_pk": 101,
            "team_id": 10,
            "side": "home",
            "game_date": early,
            "season": "2024",
            "score": 4,
            "is_winner": True,
        },
        {
            "game_pk": 101,
            "team_id": 20,
            "side": "away",
            "game_date": early,
            "season": "2024",
            "score": 2,
            "is_winner": False,
        },
        {
            "game_pk": 102,
            "team_id": 10,
            "side": "home",
            "game_date": late,
            "season": "2024",
            "score": None,
            "is_winner": None,
        },
        {
            "game_pk": 102,
            "team_id": 30,
            "side": "away",
            "game_date": late,
            "season": "2024",
            "score": None,
            "is_winner": None,
        },
    ]
    late_home = _by_key(build_team_features(rows))[(102, 10)]
    assert late_home["games_played_before"] == 1
    assert late_home["runs_scored_total_before"] == 4


def test_incomplete_intermediate_game_is_skipped_from_aggregates() -> None:
    """Postponed/incomplete rows must not enter season or rolling history."""
    rows = (
        _game_pair(
            1,
            "2024-04-01T19:00:00",
            home_id=10,
            away_id=20,
            home_score=5,
            away_score=3,
            home_won=True,
            away_won=False,
        )
        + _game_pair(
            2,
            "2024-04-02T19:00:00",
            home_id=10,
            away_id=30,
            home_score=None,
            away_score=None,
            home_won=None,
            away_won=None,
        )
        + _game_pair(
            3,
            "2024-04-03T19:00:00",
            home_id=10,
            away_id=40,
            home_score=None,
            away_score=None,
            home_won=None,
            away_won=None,
        )
    )
    features = _by_key(build_team_features(rows))

    assert features[(2, 10)]["games_played_before"] == 1
    assert features[(2, 10)]["runs_scored_total_before"] == 5
    # Game 3 still sees only game 1; incomplete game 2 never entered history.
    assert features[(3, 10)]["games_played_before"] == 1
    assert features[(3, 10)]["wins_before"] == 1
    assert features[(3, 10)]["games_L7"] == 1
    assert features[(3, 10)]["win_pct_L7"] == 1.0


def test_bool_and_missing_opponent_scores_are_excluded() -> None:
    """bool is a subclass of int in Python; True/False must not count as runs."""
    when1 = _dt("2024-04-01T19:00:00")
    when2 = _dt("2024-04-02T19:00:00")
    when3 = _dt("2024-04-03T19:00:00")
    rows = [
        {
            "game_pk": 1,
            "team_id": 10,
            "side": "home",
            "game_date": when1,
            "season": "2024",
            "score": True,
            "is_winner": True,
        },
        {
            "game_pk": 1,
            "team_id": 20,
            "side": "away",
            "game_date": when1,
            "season": "2024",
            "score": False,
            "is_winner": False,
        },
        {
            "game_pk": 2,
            "team_id": 10,
            "side": "home",
            "game_date": when2,
            "season": "2024",
            "score": 4,
            "is_winner": True,
        },
        {
            "game_pk": 2,
            "team_id": 30,
            "side": "away",
            "game_date": when2,
            "season": "2024",
            "score": None,
            "is_winner": False,
        },
        {
            "game_pk": 3,
            "team_id": 10,
            "side": "away",
            "game_date": when3,
            "season": "2024",
            "score": None,
            "is_winner": None,
        },
        {
            "game_pk": 3,
            "team_id": 40,
            "side": "home",
            "game_date": when3,
            "season": "2024",
            "score": None,
            "is_winner": None,
        },
    ]
    third = _by_key(build_team_features(rows))[(3, 10)]
    assert third["games_played_before"] == 0
    assert third["win_pct_before"] is None
    assert third["games_L7"] == 0


def test_live_partial_scores_both_losers_excluded_from_history() -> None:
    """Live/mid-game 2–0 with both is_winner=False must not pollute later features."""
    rows = (
        _game_pair(
            1,
            "2024-04-01T13:05:00",
            home_id=10,
            away_id=20,
            home_score=2,
            away_score=0,
            home_won=False,
            away_won=False,
        )
        + _game_pair(
            2,
            "2024-04-01T19:05:00",
            home_id=10,
            away_id=30,
            home_score=None,
            away_score=None,
            home_won=None,
            away_won=None,
        )
    )
    features = _by_key(build_team_features(rows))

    nightcap = features[(2, 10)]
    assert nightcap["games_played_before"] == 0
    assert nightcap["wins_before"] == 0
    assert nightcap["win_pct_before"] is None
    assert nightcap["runs_scored_total_before"] == 0
    assert nightcap["runs_allowed_total_before"] == 0
    assert nightcap["games_L7"] == 0
    assert nightcap["win_pct_L7"] is None

    # Opponent from the live game also must not carry that partial into later rows.
    assert features[(1, 20)]["games_played_before"] == 0


def test_duplicate_game_pk_team_id_raises() -> None:
    when = _dt("2024-04-01T19:00:00")
    rows = [
        {
            "game_pk": 1,
            "team_id": 10,
            "side": "home",
            "game_date": when,
            "season": "2024",
            "score": 5,
            "is_winner": True,
        },
        {
            "game_pk": 1,
            "team_id": 20,
            "side": "away",
            "game_date": when,
            "season": "2024",
            "score": 3,
            "is_winner": False,
        },
        {
            "game_pk": 1,
            "team_id": 10,
            "side": "home",
            "game_date": when,
            "season": "2024",
            "score": 5,
            "is_winner": True,
        },
    ]
    with pytest.raises(ValueError, match=r"duplicate \(game_pk, team_id\)"):
        build_team_features(rows)


def test_inconsistent_score_vs_is_winner_raises() -> None:
    rows = _game_pair(
        1,
        "2024-04-01T19:00:00",
        home_id=10,
        away_id=20,
        home_score=5,
        away_score=3,
        home_won=False,
        away_won=True,
    )
    with pytest.raises(ValueError, match="disagrees with is_winner"):
        build_team_features(rows)


def test_both_teams_marked_winner_raises() -> None:
    rows = _game_pair(
        1,
        "2024-04-01T19:00:00",
        home_id=10,
        away_id=20,
        home_score=5,
        away_score=3,
        home_won=True,
        away_won=True,
    )
    with pytest.raises(ValueError, match="both teams marked is_winner=True"):
        build_team_features(rows)


def test_league_fields_are_never_required_or_read() -> None:
    # Rows may omit league_* entirely; builder must not depend on them.
    rows = [
        {
            "game_pk": 1,
            "team_id": 10,
            "side": "home",
            "game_date": _dt("2024-04-01T19:00:00"),
            "score": 1,
            "is_winner": True,
            "league_wins": 99,
            "league_losses": 0,
            "league_pct": "1.000",
        },
        {
            "game_pk": 1,
            "team_id": 20,
            "side": "away",
            "game_date": _dt("2024-04-01T19:00:00"),
            "score": 0,
            "is_winner": False,
            "league_wins": 0,
            "league_losses": 99,
            "league_pct": ".000",
        },
        {
            "game_pk": 2,
            "team_id": 10,
            "side": "away",
            "game_date": _dt("2024-04-02T19:00:00"),
            "score": None,
            "is_winner": None,
            "league_wins": 100,
            "league_losses": 0,
            "league_pct": "1.000",
        },
        {
            "game_pk": 2,
            "team_id": 30,
            "side": "home",
            "game_date": _dt("2024-04-02T19:00:00"),
            "score": None,
            "is_winner": None,
        },
    ]
    second = _by_key(build_team_features(rows))[(2, 10)]
    # Features come from prior score/is_winner only — not inflated league_wins.
    assert second["games_played_before"] == 1
    assert second["wins_before"] == 1
    assert "league_wins" not in second
