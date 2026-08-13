from datetime import datetime, timezone

from scripts.daily_predictions import (
    _bullpen_placeholder_rows,
    _starter_placeholder_rows,
    odds_snapshots_for_schedule,
)
from features.bullpen import build_bullpen_features
from features.starter import build_starter_features


def _schedule_row(game_pk=1):
    return {
        "game_pk": game_pk,
        "season": "2026",
        "game_type": "R",
        "game_date": datetime(2026, 8, 12, 23, 5),
        "game_start_timestamp": datetime(2026, 8, 12, 23, 5),
        "official_date": "2026-08-12",
        "home_team_id": 110,
        "away_team_id": 142,
        "game_number": 1,
        "abstract_game_state": "Preview",
        "detailed_state": "Scheduled",
        "source_game_json": {
            "teams": {
                "home": {"team": {"name": "Minnesota Twins"}},
                "away": {"team": {"name": "Baltimore Orioles"}},
            }
        },
    }


def test_odds_snapshots_map_requested_bookmaker_to_mlb_game_pk():
    payload = [
        {
            "id": "event-1",
            "commence_time": "2026-08-12T23:05:00Z",
            "home_team": "Minnesota Twins",
            "away_team": "Baltimore Orioles",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "last_update": "2026-08-12T20:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Minnesota Twins", "price": -105},
                                {"name": "Baltimore Orioles", "price": -115},
                            ],
                        }
                    ],
                },
                {
                    "key": "draftkings",
                    "last_update": "2026-08-12T20:01:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Minnesota Twins", "price": -110},
                                {"name": "Baltimore Orioles", "price": -110},
                            ],
                        }
                    ],
                },
            ],
        }
    ]

    snapshots, stats = odds_snapshots_for_schedule(
        payload, [_schedule_row(823672)], bookmaker="draftkings"
    )

    assert stats["mapped_games"] == 1
    assert snapshots[823672]["home_american"] == -110
    assert snapshots[823672]["away_american"] == -110
    assert snapshots[823672]["snapshot_timestamp"] == datetime(
        2026, 8, 12, 20, 1, tzinfo=timezone.utc
    )
    assert snapshots[823672]["source"] == "the_odds_api:draftkings"


def test_odds_snapshots_match_same_teams_with_start_time_drift():
    payload = [
        {
            "id": "event-1",
            "commence_time": "2026-08-12T23:05:00Z",
            "home_team": "Minnesota Twins",
            "away_team": "Baltimore Orioles",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "last_update": "2026-08-12T20:01:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Minnesota Twins", "price": -110},
                                {"name": "Baltimore Orioles", "price": -110},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    drifted_schedule = [_schedule_row(823672)]
    drifted_schedule[0]["game_date"] = datetime(2026, 8, 12, 22, 5)

    snapshots, stats = odds_snapshots_for_schedule(
        payload, drifted_schedule, bookmaker="draftkings"
    )

    assert stats["mapped_games"] == 1
    assert snapshots[823672]["home_american"] == -110


def test_odds_snapshots_leave_unmatched_events_visible():
    payload = [
        {
            "id": "event-1",
            "commence_time": "2026-08-12T23:05:00Z",
            "home_team": "Minnesota Twins",
            "away_team": "Baltimore Orioles",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "last_update": "2026-08-12T20:01:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Minnesota Twins", "price": -110},
                                {"name": "Baltimore Orioles", "price": -110},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    wrong_time_schedule = [_schedule_row(823672)]
    wrong_time_schedule[0]["game_date"] = datetime(2026, 8, 13, 12, 30)

    snapshots, stats = odds_snapshots_for_schedule(
        payload, wrong_time_schedule, bookmaker="draftkings"
    )

    assert snapshots == {}
    assert stats["unmatched_events.time_out_of_tolerance"] == 1


def test_bullpen_placeholders_emit_current_pregame_rows_without_current_bullpen_stats():
    schedule = [_schedule_row(823672)]
    history = [
        {
            "game_pk": 1,
            "team_id": 110,
            "side": "home",
            "game_date": datetime(2026, 8, 10, 23, 5),
            "game_type": "R",
            "game_number": 1,
            "pitcher_id": 100,
            "appearance_order": 1,
            "is_actual_starter": True,
            "outs_recorded": 18,
            "pitches_thrown": 80,
            "earned_runs": 1,
            "hits_allowed": 3,
            "walks": 1,
        },
        {
            "game_pk": 1,
            "team_id": 110,
            "side": "home",
            "game_date": datetime(2026, 8, 10, 23, 5),
            "game_type": "R",
            "game_number": 1,
            "pitcher_id": 101,
            "appearance_order": 2,
            "is_actual_starter": False,
            "outs_recorded": 3,
            "pitches_thrown": 12,
            "earned_runs": 0,
            "hits_allowed": 1,
            "walks": 0,
        },
    ]

    rows = build_bullpen_features(history + _bullpen_placeholder_rows(schedule))
    current_home = next(row for row in rows if row["game_pk"] == 823672 and row["team_id"] == 110)
    current_away = next(row for row in rows if row["game_pk"] == 823672 and row["team_id"] == 142)

    assert current_home["bullpen_games_L7"] == 1
    assert current_home["bullpen_appearances_L7"] == 1
    assert current_home["bullpen_outs_prior_3d"] == 3
    assert current_away["bullpen_games_L7"] == 0
    assert current_away["bullpen_era_L7"] is None


def test_schedule_match_keeps_naive_game_date_but_pipeline_start_is_aware():
    row = _schedule_row(823672)
    # Feature builders consume naive DuckDB game_date values; the daily pipeline
    # cutoff comparison needs game_start_timestamp normalized to UTC-aware.
    from scripts.daily_predictions import _utc_instant

    row["game_start_timestamp"] = _utc_instant(row["game_start_timestamp"])

    assert row["game_date"].tzinfo is None
    assert row["game_start_timestamp"].tzinfo is timezone.utc


def test_starter_placeholders_emit_unknown_starter_features_for_live_gaps():
    schedule = [_schedule_row(823672)]
    starters = [
        {
            "game_pk": 823672,
            "team_id": 142,
            "side": "home",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 9001,
        }
    ]

    placeholders = _starter_placeholder_rows(schedule, starters)
    rows = build_starter_features([], schedule, starters + placeholders)
    by_team = {(row["game_pk"], row["team_id"]): row for row in rows}

    assert len(placeholders) == 1
    assert by_team[(823672, 142)]["starter_known"] is True
    assert by_team[(823672, 142)]["starter_is_probable"] is True
    assert by_team[(823672, 110)]["starter_known"] is False
    assert by_team[(823672, 110)]["starter_pitcher_id"] is None
