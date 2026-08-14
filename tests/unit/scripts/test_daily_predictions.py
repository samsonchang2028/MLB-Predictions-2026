import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from scripts import daily_predictions as dp
from scripts.daily_predictions import (
    PENDING_STARTER_MESSAGE,
    SKIP_NO_STARTER_ANNOUNCED,
    _bullpen_placeholder_rows,
    _format_duration,
    _runs_by_game_pk,
    _starter_is_announced,
    _starter_placeholder_rows,
    all_book_snapshots_for_schedule,
    append_jsonl_records,
    build_simulation_records,
    main,
    odds_snapshots_for_schedule,
    partition_schedule_by_announced_starters,
    refresh_pregame_game_details,
    replace_skipped_for_run_date,
    score_training_rows_from_matrix,
    slate_game_pks_for_detail_refresh,
    totals_lines_for_schedule,
)
from features.bullpen import build_bullpen_features
from features.starter import build_starter_features
from simulation.score_model import fit_score_model


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


def test_all_book_snapshots_keeps_every_bookmaker_unlike_the_single_book_filter():
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

    snapshots, stats = all_book_snapshots_for_schedule(payload, [_schedule_row(823672)])

    assert stats["mapped_game_books"] == 2
    assert snapshots[(823672, "fanduel")]["home_american"] == -105
    assert snapshots[(823672, "draftkings")]["home_american"] == -110
    assert snapshots[(823672, "fanduel")]["source"] == "the_odds_api"


def test_all_book_snapshots_latest_timestamp_wins_per_book():
    payload = [
        {
            "id": "event-1",
            "commence_time": "2026-08-12T23:05:00Z",
            "home_team": "Minnesota Twins",
            "away_team": "Baltimore Orioles",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "last_update": "2026-08-12T19:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Minnesota Twins", "price": -108},
                                {"name": "Baltimore Orioles", "price": -112},
                            ],
                        }
                    ],
                }
            ],
        },
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
        },
    ]

    snapshots, _stats = all_book_snapshots_for_schedule(payload, [_schedule_row(823672)])

    assert snapshots[(823672, "draftkings")]["home_american"] == -110
    assert snapshots[(823672, "draftkings")]["snapshot_timestamp"] == datetime(
        2026, 8, 12, 20, 1, tzinfo=timezone.utc
    )


def test_append_jsonl_records_is_idempotent_and_conflict_checked(tmp_path):
    path = tmp_path / "detail.jsonl"
    record = {"run_date": "2026-08-12", "game_pk": 1, "bookmaker": "draftkings", "price": -110}

    written_first = append_jsonl_records(path, [record], key_fields=("run_date", "game_pk", "bookmaker"))
    written_second = append_jsonl_records(path, [record], key_fields=("run_date", "game_pk", "bookmaker"))

    assert written_first == 1
    assert written_second == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    conflicting = {**record, "price": -120}
    with pytest.raises(ValueError, match="conflicting re-write"):
        append_jsonl_records(path, [conflicting], key_fields=("run_date", "game_pk", "bookmaker"))


def test_append_jsonl_records_overwrite_mode_updates_existing_key_in_place(tmp_path):
    path = tmp_path / "detail.jsonl"
    record = {"run_date": "2026-08-12", "game_pk": 1, "bookmaker": "draftkings", "price": -110}
    other = {"run_date": "2026-08-12", "game_pk": 2, "bookmaker": "draftkings", "price": -105}

    append_jsonl_records(path, [record, other], key_fields=("run_date", "game_pk", "bookmaker"), on_conflict="overwrite")

    moved = {**record, "price": -130}
    written = append_jsonl_records(path, [moved], key_fields=("run_date", "game_pk", "bookmaker"), on_conflict="overwrite")

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    by_game = {row["game_pk"]: row for row in lines}

    assert written == 1
    assert len(lines) == 2
    assert by_game[1]["price"] == -130
    assert by_game[2]["price"] == -105


def test_append_jsonl_records_overwrite_updates_changed_game_features(tmp_path):
    path = tmp_path / "features.jsonl"
    first = {
        "run_date": "2026-08-13",
        "game_pk": 823508,
        "build_id": "a910017bac839af5",
        "features": {"home_starter_starter_known": False},
    }
    updated = {**first, "features": {"home_starter_starter_known": True}}

    append_jsonl_records(
        path, [first], key_fields=("run_date", "game_pk", "build_id"), on_conflict="overwrite"
    )
    written = append_jsonl_records(
        path, [updated], key_fields=("run_date", "game_pk", "build_id"), on_conflict="overwrite"
    )

    assert written == 1
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["features"]["home_starter_starter_known"] is True


def test_format_duration_renders_minutes_and_seconds():
    assert _format_duration(5.4) == "5s"
    assert _format_duration(65) == "1m 5s"
    assert _format_duration(330) == "5m 30s"
    assert _format_duration(3665) == "1h 1m 5s"


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


def test_starter_is_announced_requires_probable_or_actual_id():
    assert _starter_is_announced(None) is False
    assert _starter_is_announced({"actual_pitcher_id": None, "probable_pitcher_id": None}) is False
    assert _starter_is_announced({"actual_pitcher_id": None, "probable_pitcher_id": 42}) is True
    assert _starter_is_announced({"actual_pitcher_id": 7, "probable_pitcher_id": None}) is True


def test_partition_schedule_withholds_games_missing_either_starter():
    schedule = [_schedule_row(823915)]
    schedule[0].update({"home_team_id": 119, "away_team_id": 158})
    starters = [
        {
            "game_pk": 823915,
            "team_id": 158,
            "side": "away",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 675660,
        }
    ]

    ready, skipped = partition_schedule_by_announced_starters(
        schedule, starters, run_date=__import__("datetime").date(2026, 8, 13)
    )

    assert ready == []
    assert len(skipped) == 1
    assert skipped[0]["game_pk"] == 823915
    assert skipped[0]["reason"] == SKIP_NO_STARTER_ANNOUNCED
    assert skipped[0]["missing_team_ids"] == [119]
    assert skipped[0]["message"] == PENDING_STARTER_MESSAGE


def test_partition_schedule_keeps_game_when_both_starters_announced():
    schedule = [_schedule_row(823915)]
    schedule[0].update({"home_team_id": 119, "away_team_id": 158})
    starters = [
        {
            "game_pk": 823915,
            "team_id": 158,
            "side": "away",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 675660,
        },
        {
            "game_pk": 823915,
            "team_id": 119,
            "side": "home",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 808967,
        },
    ]

    ready, skipped = partition_schedule_by_announced_starters(
        schedule, starters, run_date=__import__("datetime").date(2026, 8, 13)
    )

    assert len(ready) == 1
    assert skipped == []


def test_replace_skipped_for_run_date_rewrites_only_target_slate(tmp_path):
    path = tmp_path / "skipped.jsonl"
    path.write_text(
        json.dumps({"run_date": "2026-08-12", "game_pk": 1, "reason": "other"}) + "\n",
        encoding="utf-8",
    )

    written = replace_skipped_for_run_date(
        path,
        __import__("datetime").date(2026, 8, 13),
        [
            {
                "run_date": "2026-08-13",
                "game_pk": 823915,
                "reason": SKIP_NO_STARTER_ANNOUNCED,
                "message": PENDING_STARTER_MESSAGE,
            }
        ],
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert written == 1
    assert {row["game_pk"] for row in rows} == {1, 823915}


def _write_slate_games(database: Path, rows: list[tuple[int, str, date]]) -> None:
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS silver.games (
                game_pk BIGINT,
                game_type VARCHAR,
                official_date DATE,
                abstract_game_state VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO silver.games VALUES (?, 'R', ?, ?)",
            [(game_pk, official_date, state) for game_pk, state, official_date in rows],
        )


def test_slate_game_pks_for_detail_refresh_targets_preview_and_live_only(tmp_path):
    database = tmp_path / "mlb.duckdb"
    run_date = date(2026, 8, 12)
    _write_slate_games(
        database,
        [
            (101, "Preview", run_date),
            (102, "Live", run_date),
            (103, "Final", run_date),
            (104, "Preview", date(2026, 8, 13)),
        ],
    )

    assert slate_game_pks_for_detail_refresh(database, run_date) == [101, 102]


def test_refresh_pregame_game_details_invalidates_backfills_and_normalizes(
    monkeypatch, tmp_path
):
    database = tmp_path / "mlb.duckdb"
    run_date = date(2026, 8, 12)
    _write_slate_games(database, [(823915, "Preview", run_date), (823916, "Live", run_date)])

    calls: dict[str, object] = {}

    def fake_invalidate(storage_root, game_pks):
        calls["storage_root"] = storage_root
        calls["invalidate_game_pks"] = list(game_pks)
        return len(game_pks)

    def fake_backfill(storage_root, fetcher, **kwargs):
        calls["backfill_kwargs"] = kwargs
        calls["fetcher"] = fetcher
        return {"fetched": 2, "targeted": 2}

    def fake_normalize(connection):
        calls["normalized"] = True
        return {"pitcher_starters": 4}

    monkeypatch.setattr(dp, "invalidate_game_detail_payloads", fake_invalidate)
    monkeypatch.setattr(dp, "backfill_game_details", fake_backfill)
    monkeypatch.setattr(dp, "normalize_silver", fake_normalize)
    monkeypatch.setattr(dp, "connect_database", duckdb.connect)

    fetcher = object()
    stats = refresh_pregame_game_details(database, run_date, fetcher)

    assert calls["storage_root"] == tmp_path
    assert calls["invalidate_game_pks"] == [823915, 823916]
    assert calls["backfill_kwargs"]["game_pks"] == [823915, 823916]
    assert calls["backfill_kwargs"]["retry_unresolved"] is True
    assert calls["fetcher"] is fetcher
    assert calls["normalized"] is True
    assert stats == {
        "targeted": 2,
        "invalidated": 2,
        "fetched": 2,
        "normalized": True,
        "game_pks": [823915, 823916],
        "backfill": {"fetched": 2, "targeted": 2},
    }


def test_refresh_pregame_game_details_noops_when_slate_empty(monkeypatch, tmp_path):
    database = tmp_path / "mlb.duckdb"
    _write_slate_games(database, [(103, "Final", date(2026, 8, 12))])

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network/backfill path should not run for empty slate")

    monkeypatch.setattr(dp, "invalidate_game_detail_payloads", fail_if_called)
    monkeypatch.setattr(dp, "backfill_game_details", fail_if_called)
    monkeypatch.setattr(dp, "normalize_silver", fail_if_called)

    stats = refresh_pregame_game_details(database, date(2026, 8, 12), object())

    assert stats == {
        "targeted": 0,
        "invalidated": 0,
        "fetched": 0,
        "normalized": False,
    }


def test_main_skip_detail_refresh_bypasses_backfill(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setattr(
        dp,
        "train_locked_model",
        lambda *args, **kwargs: (object(), ["f1"], "build", 10),
    )
    refresh_calls: list[tuple] = []

    def fake_refresh(*args, **kwargs):
        refresh_calls.append((args, kwargs))
        return {
            "targeted": 0,
            "invalidated": 0,
            "fetched": 0,
            "normalized": False,
        }

    monkeypatch.setattr(dp, "refresh_pregame_game_details", fake_refresh)
    monkeypatch.setattr(
        dp,
        "load_prediction_inputs",
        lambda *args, **kwargs: {
            "certification": {},
            "schedule": [],
            "games_for_features": [],
            "games_for_today": [],
            "team_stats": [],
            "appearances": [],
            "starters": [],
        },
    )

    assert main(["--skip-detail-refresh", "--database", str(tmp_path / "mlb.duckdb")]) == 0
    assert refresh_calls == []
    assert "[detail-refresh] skipped" in capsys.readouterr().out


def test_main_runs_detail_refresh_before_loading_inputs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setattr(
        dp,
        "train_locked_model",
        lambda *args, **kwargs: (object(), ["f1"], "build", 10),
    )
    call_order: list[str] = []

    def fake_refresh(*args, **kwargs):
        call_order.append("refresh")
        return {
            "targeted": 1,
            "invalidated": 1,
            "fetched": 1,
            "normalized": True,
        }

    def fake_load(*args, **kwargs):
        call_order.append("load")
        return {
            "certification": {},
            "schedule": [],
            "games_for_features": [],
            "games_for_today": [],
            "team_stats": [],
            "appearances": [],
            "starters": [],
        }

    monkeypatch.setattr(dp, "refresh_pregame_game_details", fake_refresh)
    monkeypatch.setattr(dp, "load_prediction_inputs", fake_load)
    monkeypatch.setattr(dp, "make_game_detail_fetcher", lambda: object())

    assert main(["--database", str(tmp_path / "mlb.duckdb")]) == 0
    assert call_order == ["refresh", "load"]
    assert "[detail-refresh] targeted=1 invalidated=1 fetched=1 normalized=True" in capsys.readouterr().out


def test_main_sets_prediction_timestamp_after_odds_fetch(monkeypatch, tmp_path):
    """Regression: a long pre-odds phase must not freeze prediction_timestamp too early."""
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    events: list[str] = []
    odds_ts = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)
    pred_ts = datetime(2026, 8, 13, 20, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(
        dp,
        "train_locked_model",
        lambda *args, **kwargs: (object(), ["f1"], "build", 10),
    )
    monkeypatch.setattr(
        dp,
        "refresh_pregame_game_details",
        lambda *args, **kwargs: {
            "targeted": 0,
            "invalidated": 0,
            "fetched": 0,
            "normalized": False,
        },
    )
    schedule_row = {
        "game_pk": 823915,
        "season": "2026",
        "game_type": "R",
        "game_date": datetime(2026, 8, 14, 2, 10),
        "game_start_timestamp": datetime(2026, 8, 14, 2, 10, tzinfo=timezone.utc),
        "official_date": date(2026, 8, 13),
        "home_team_id": 119,
        "away_team_id": 158,
        "game_number": 1,
        "abstract_game_state": "Preview",
        "detailed_state": "Scheduled",
        "source_game_json": "{}",
    }
    starter_rows = [
        {
            "game_pk": 823915,
            "team_id": 119,
            "side": "home",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 808963,
        },
        {
            "game_pk": 823915,
            "team_id": 158,
            "side": "away",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 675660,
        },
    ]
    monkeypatch.setattr(
        dp,
        "load_prediction_inputs",
        lambda *args, **kwargs: {
            "certification": {"build_id": "build"},
            "schedule": [schedule_row],
            "games_for_features": [],
            "games_for_today": [],
            "team_stats": [],
            "appearances": [],
            "starters": starter_rows,
        },
    )
    monkeypatch.setattr(
        dp,
        "build_today_feature_components",
        lambda inputs: {"team": [], "starter": [], "bullpen": []},
    )
    monkeypatch.setattr(
        dp,
        "build_feature_matrix",
        lambda *args, **kwargs: {
            "build_id": "build",
            "rows": [
                {
                    "game_pk": 823915,
                    "features": {"f1": 1.0},
                }
            ],
            "excluded": [],
            "feature_columns": ["f1"],
            "feature_completeness": {"status": "PASS"},
        },
    )

    def fake_fetch_odds(*args, **kwargs):
        events.append("odds_fetch")
        return []

    monkeypatch.setattr(dp, "fetch_odds_payload", fake_fetch_odds)
    monkeypatch.setattr(
        dp,
        "odds_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: (
            {
                823915: {
                    "home_american": -140,
                    "away_american": 131,
                    "snapshot_timestamp": odds_ts,
                    "source": "the_odds_api:draftkings",
                }
            },
            {"mapped_games": 1},
        ),
    )
    monkeypatch.setattr(
        dp,
        "all_book_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: ({}, {"mapped_game_books": 0}),
    )
    monkeypatch.setattr(dp, "parse_timestamp", lambda value: (events.append("prediction_timestamp"), pred_ts)[1])

    captured: dict[str, datetime] = {}

    def fake_run_daily_predictions(**kwargs):
        captured["prediction_timestamp"] = kwargs["prediction_timestamp"]
        class Result:
            records = ()
            written = ()
            skipped = ()

        return Result()

    monkeypatch.setattr(dp, "run_daily_predictions", fake_run_daily_predictions)
    monkeypatch.setattr(dp, "append_jsonl_records", lambda *args, **kwargs: 0)
    monkeypatch.setattr(dp, "JsonLinesPredictionStore", lambda path: object())
    monkeypatch.setattr(
        dp,
        "train_locked_score_model",
        lambda *args, **kwargs: (object(), "hist-build", 100),
    )

    assert dp.main(
        [
            "--skip-detail-refresh",
            "--database",
            str(tmp_path / "mlb.duckdb"),
            "--date",
            "2026-08-13",
        ]
    ) == 0
    assert events.index("odds_fetch") < events.index("prediction_timestamp")
    assert captured["prediction_timestamp"] == pred_ts
    assert odds_ts < pred_ts


def _minimal_score_training_rows() -> list[dict]:
    rows: list[dict] = []
    for i in range(30):
        offense = 4.0 + (i % 4) * 0.2
        rows.append(
            {
                "features": {
                    "home_team_runs_scored_avg_before": offense,
                    "home_team_runs_scored_avg_L7": offense - 0.1,
                    "away_team_runs_scored_avg_before": offense - 0.2,
                    "away_team_runs_scored_avg_L7": offense - 0.3,
                    "home_team_runs_allowed_avg_before": offense - 0.1,
                    "home_team_runs_allowed_avg_L7": offense - 0.2,
                    "away_team_runs_allowed_avg_before": offense,
                    "away_team_runs_allowed_avg_L7": offense - 0.1,
                },
                "home_runs": int(round(offense)),
                "away_runs": int(round(offense - 0.5)),
            }
        )
    return rows


def test_runs_by_game_pk_extracts_home_and_away_scores():
    team_stats = [
        {"game_pk": 1, "team_id": 10, "side": "home", "score": 5},
        {"game_pk": 1, "team_id": 20, "side": "away", "score": 3},
        {"game_pk": 2, "team_id": 11, "side": "home", "score": None},
        {"game_pk": 2, "team_id": 21, "side": "away", "score": 2},
    ]

    assert _runs_by_game_pk(team_stats) == {1: (5, 3)}


def test_score_training_rows_from_matrix_joins_gold_features_to_final_scores():
    matrix = {
        "rows": [
            {"game_pk": 100, "features": {"f1": 1.0}},
            {"game_pk": 101, "features": {"f1": 2.0}},
        ]
    }
    team_stats = [
        {"game_pk": 100, "side": "home", "score": 4},
        {"game_pk": 100, "side": "away", "score": 2},
    ]

    rows = score_training_rows_from_matrix(matrix, team_stats)

    assert len(rows) == 1
    assert rows[0]["features"] == {"f1": 1.0}
    assert rows[0]["home_runs"] == 4
    assert rows[0]["away_runs"] == 2


def test_build_simulation_records_shapes_operator_artifact_fields():
    score_model = fit_score_model(_minimal_score_training_rows(), random_state=0)
    feature_rows = [
        {
            "game_pk": 823915,
            "features": _minimal_score_training_rows()[0]["features"],
        }
    ]

    records = build_simulation_records(
        feature_rows,
        score_model=score_model,
        run_date=date(2026, 8, 13),
        build_id="gold-build-abc",
        n_trials=500,
        random_state=7,
        totals_lines={823915: 8.5},
    )

    assert len(records) == 1
    row = records[0]
    assert row["run_date"] == "2026-08-13"
    assert row["game_pk"] == 823915
    assert row["model_version"] == "sim-game-level-v1"
    assert row["build_id"] == "gold-build-abc"
    assert row["n_trials"] == 500
    assert 0.0 <= row["p_home_win"] <= 1.0
    assert row["total_runs_mean"] == pytest.approx(
        row["home_runs_mean"] + row["away_runs_mean"]
    )
    assert row["totals_line"] == 8.5
    assert row["p_over"] + row["p_under"] <= 1.0


def test_totals_lines_for_schedule_maps_primary_bookmaker_line_to_game_pk():
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
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 8.5},
                                {"name": "Under", "price": -110, "point": 8.5},
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "last_update": "2026-08-12T20:01:00Z",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -105, "point": 9.0},
                                {"name": "Under", "price": -115, "point": 9.0},
                            ],
                        }
                    ],
                },
            ],
        }
    ]

    lines, stats = totals_lines_for_schedule(
        payload, [_schedule_row(823672)], bookmaker="draftkings"
    )
    fanduel_lines, _ = totals_lines_for_schedule(
        payload, [_schedule_row(823672)], bookmaker="fanduel"
    )

    assert stats["mapped_games"] == 1
    assert lines[823672] == 8.5
    assert fanduel_lines[823672] == 9.0


def test_main_skips_simulation_by_default(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setattr(
        dp,
        "train_locked_model",
        lambda *args, **kwargs: (object(), ["f1"], "build", 10),
    )
    fit_calls: list[tuple] = []

    def fail_fit(*args, **kwargs):
        fit_calls.append((args, kwargs))
        raise AssertionError("score model fit should not run unless --enable-simulation")

    monkeypatch.setattr(dp, "train_locked_score_model", fail_fit)
    monkeypatch.setattr(
        dp,
        "refresh_pregame_game_details",
        lambda *args, **kwargs: {
            "targeted": 0,
            "invalidated": 0,
            "fetched": 0,
            "normalized": False,
        },
    )
    schedule_row = _schedule_row(823915)
    schedule_row.update({"home_team_id": 119, "away_team_id": 158})
    starter_rows = [
        {
            "game_pk": 823915,
            "team_id": 119,
            "side": "home",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 808963,
        },
        {
            "game_pk": 823915,
            "team_id": 158,
            "side": "away",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 675660,
        },
    ]
    monkeypatch.setattr(
        dp,
        "load_prediction_inputs",
        lambda *args, **kwargs: {
            "certification": {"build_id": "build"},
            "schedule": [schedule_row],
            "games_for_features": [],
            "games_for_today": [],
            "team_stats": [],
            "appearances": [],
            "starters": starter_rows,
        },
    )
    monkeypatch.setattr(
        dp,
        "build_today_feature_components",
        lambda inputs: {"team": [], "starter": [], "bullpen": []},
    )
    monkeypatch.setattr(
        dp,
        "build_feature_matrix",
        lambda *args, **kwargs: {
            "build_id": "build",
            "rows": [{"game_pk": 823915, "features": {"f1": 1.0}}],
            "excluded": [],
            "feature_columns": ["f1"],
            "feature_completeness": {"status": "PASS"},
        },
    )
    monkeypatch.setattr(dp, "fetch_odds_payload", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        dp,
        "odds_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: ({}, {"mapped_games": 0}),
    )
    monkeypatch.setattr(
        dp,
        "all_book_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: ({}, {"mapped_game_books": 0}),
    )
    monkeypatch.setattr(
        dp,
        "run_daily_predictions",
        lambda **kwargs: type(
            "Result",
            (),
            {"records": (), "written": (), "skipped": ()},
        )(),
    )
    monkeypatch.setattr(dp, "append_jsonl_records", lambda *args, **kwargs: 0)
    monkeypatch.setattr(dp, "JsonLinesPredictionStore", lambda path: object())

    assert main(
        [
            "--skip-detail-refresh",
            "--database",
            str(tmp_path / "mlb.duckdb"),
            "--date",
            "2026-08-13",
        ]
    ) == 0
    assert fit_calls == []
    assert "[simulation] skipped" in capsys.readouterr().out


def test_main_writes_simulation_jsonl_with_mocked_score_model(monkeypatch, tmp_path):
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setattr(
        dp,
        "train_locked_model",
        lambda *args, **kwargs: (object(), ["f1"], "build", 10),
    )
    monkeypatch.setattr(
        dp,
        "refresh_pregame_game_details",
        lambda *args, **kwargs: {
            "targeted": 0,
            "invalidated": 0,
            "fetched": 0,
            "normalized": False,
        },
    )
    schedule_row = _schedule_row(823915)
    schedule_row.update({"home_team_id": 119, "away_team_id": 158})
    starter_rows = [
        {
            "game_pk": 823915,
            "team_id": 119,
            "side": "home",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 808963,
        },
        {
            "game_pk": 823915,
            "team_id": 158,
            "side": "away",
            "actual_pitcher_id": None,
            "probable_pitcher_id": 675660,
        },
    ]
    monkeypatch.setattr(
        dp,
        "load_prediction_inputs",
        lambda *args, **kwargs: {
            "certification": {"build_id": "build"},
            "schedule": [schedule_row],
            "games_for_features": [],
            "games_for_today": [],
            "team_stats": [],
            "appearances": [],
            "starters": starter_rows,
        },
    )
    monkeypatch.setattr(
        dp,
        "build_today_feature_components",
        lambda inputs: {"team": [], "starter": [], "bullpen": []},
    )
    monkeypatch.setattr(
        dp,
        "build_feature_matrix",
        lambda *args, **kwargs: {
            "build_id": "gold-build-xyz",
            "rows": [{"game_pk": 823915, "features": {"f1": 1.0}}],
            "excluded": [],
            "feature_columns": ["f1"],
            "feature_completeness": {"status": "PASS"},
        },
    )
    monkeypatch.setattr(
        dp,
        "train_locked_score_model",
        lambda *args, **kwargs: (object(), "hist-build", 100),
    )
    monkeypatch.setattr(
        dp,
        "build_simulation_records",
        lambda *args, **kwargs: [
            {
                "run_date": "2026-08-13",
                "game_pk": 823915,
                "p_home_win": 0.53,
                "home_runs_mean": 4.5,
                "away_runs_mean": 4.2,
                "total_runs_mean": 8.7,
                "total_runs_median": 9.0,
                "n_trials": 10000,
                "model_version": "sim-game-level-v1",
                "build_id": "gold-build-xyz",
            }
        ],
    )
    monkeypatch.setattr(dp, "fetch_odds_payload", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        dp,
        "odds_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: ({}, {"mapped_games": 0}),
    )
    monkeypatch.setattr(
        dp,
        "all_book_snapshots_for_schedule",
        lambda payload, schedule, **kwargs: ({}, {"mapped_game_books": 0}),
    )
    monkeypatch.setattr(
        dp,
        "run_daily_predictions",
        lambda **kwargs: type(
            "Result",
            (),
            {"records": (), "written": (), "skipped": ()},
        )(),
    )
    monkeypatch.setattr(dp, "JsonLinesPredictionStore", lambda path: object())

    simulation_path = tmp_path / "simulation.jsonl"
    features_path = tmp_path / "features.jsonl"
    odds_books_path = tmp_path / "odds_books.jsonl"
    append_calls: list[tuple] = []
    real_append = dp.append_jsonl_records

    def capture_append(path, records, **kwargs):
        append_calls.append((path, list(records), kwargs))
        return real_append(path, records, **kwargs)

    monkeypatch.setattr(dp, "append_jsonl_records", capture_append)

    assert (
        dp.main(
            [
                "--skip-detail-refresh",
                "--enable-simulation",
                "--database",
                str(tmp_path / "mlb.duckdb"),
                "--date",
                "2026-08-13",
                "--simulation-output",
                str(simulation_path),
                "--features-output",
                str(features_path),
                "--odds-books-output",
                str(odds_books_path),
            ]
        )
        == 0
    )

    assert simulation_path.exists()
    row = json.loads(simulation_path.read_text(encoding="utf-8").strip())
    assert row["game_pk"] == 823915
    assert row["model_version"] == "sim-game-level-v1"
    simulation_calls = [call for call in append_calls if call[0] == simulation_path]
    assert simulation_calls
    assert simulation_calls[0][2]["key_fields"] == ("run_date", "game_pk")
    assert simulation_calls[0][2]["on_conflict"] == "overwrite"
