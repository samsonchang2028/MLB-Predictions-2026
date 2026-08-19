import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from ingestion.kalshi.matching import KalshiGameCandidate, normalize_kalshi_team_name
from scripts.kalshi_pregame_capture import (
    DEFAULT_WINDOW_END_MINUTES,
    DEFAULT_WINDOW_START_MINUTES,
    already_captured_game_pks,
    build_kalshi_capture_records,
    games_due_for_capture,
    main,
)

EVENT_TIME = datetime(2026, 8, 15, 19, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Near-first-pitch timing logic: each game gets its own due window
# ---------------------------------------------------------------------------


def _game(game_pk: int, start: datetime) -> dict:
    return {"game_pk": game_pk, "game_start_timestamp": start}


def test_games_due_for_capture_each_game_has_its_own_window() -> None:
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    schedule = [
        _game(1, now + timedelta(minutes=30)),  # inside the default 15-60 min window
        _game(2, now + timedelta(minutes=90)),  # too early -- not "near" first pitch yet
        _game(3, now + timedelta(minutes=5)),  # too close to first pitch (past the floor)
        _game(4, now - timedelta(minutes=5)),  # already started
    ]

    due = games_due_for_capture(schedule, now=now, already_captured=set())

    assert [g["game_pk"] for g in due] == [1]


def test_games_due_for_capture_window_boundaries_are_inclusive() -> None:
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    schedule = [
        _game(1, now + timedelta(minutes=DEFAULT_WINDOW_START_MINUTES)),
        _game(2, now + timedelta(minutes=DEFAULT_WINDOW_END_MINUTES)),
    ]

    due = games_due_for_capture(schedule, now=now, already_captured=set())

    assert {g["game_pk"] for g in due} == {1, 2}


def test_games_due_for_capture_skips_already_captured_game() -> None:
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    schedule = [_game(1, now + timedelta(minutes=30))]

    due = games_due_for_capture(schedule, now=now, already_captured={1})

    assert due == []


def test_games_due_for_capture_just_outside_upper_bound_is_excluded() -> None:
    # One minute earlier than the 60-minute ceiling: not "near" first pitch yet.
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    schedule = [_game(1, now + timedelta(minutes=DEFAULT_WINDOW_START_MINUTES + 1))]

    due = games_due_for_capture(schedule, now=now, already_captured=set())

    assert due == []


def test_games_due_for_capture_just_inside_lower_floor_is_excluded() -> None:
    # One minute past the 15-minute floor: too close to first pitch, should
    # have already been captured by an earlier invocation, not captured now.
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    schedule = [_game(1, now + timedelta(minutes=DEFAULT_WINDOW_END_MINUTES - 1))]

    due = games_due_for_capture(schedule, now=now, already_captured=set())

    assert due == []


def test_already_captured_game_pks_filters_by_bookmaker_and_run_date(tmp_path: Path) -> None:
    path = tmp_path / "odds_books.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"run_date": "2026-08-15", "game_pk": 1, "bookmaker": "kalshi"},
                {"run_date": "2026-08-15", "game_pk": 2, "bookmaker": "draftkings"},
                {"run_date": "2026-08-14", "game_pk": 3, "bookmaker": "kalshi"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert already_captured_game_pks(path, date(2026, 8, 15)) == {1}


def test_already_captured_game_pks_missing_file_is_empty(tmp_path: Path) -> None:
    assert already_captured_game_pks(tmp_path / "missing.jsonl", date(2026, 8, 15)) == set()


# ---------------------------------------------------------------------------
# Failure isolation: one bad market/game must never affect another
# ---------------------------------------------------------------------------


def _market_row(
    ticker: str,
    event_ticker: str,
    side: str,
    no_sub_title: str,
    *,
    yes_bid: float,
    yes_ask: float,
    occurrence_datetime: datetime = EVENT_TIME,
    snapshot_timestamp: datetime | None = None,
) -> dict:
    return {
        "market_ticker": ticker,
        "event_ticker": event_ticker,
        "side": side,
        "no_sub_title": no_sub_title,
        "occurrence_datetime": occurrence_datetime,
        "yes_bid": Decimal(str(yes_bid)),
        "yes_ask": Decimal(str(yes_ask)),
        "snapshot_timestamp": snapshot_timestamp or (occurrence_datetime - timedelta(minutes=45)),
    }


def _candidate(game_pk: int, home: str, away: str, start_time: datetime = EVENT_TIME) -> KalshiGameCandidate:
    return KalshiGameCandidate(
        game_pk=game_pk,
        start_time=start_time,
        home_team_norm=normalize_kalshi_team_name(home),
        away_team_norm=normalize_kalshi_team_name(away),
    )


def test_build_records_matches_both_sides_and_converts_to_american() -> None:
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]
    now = EVENT_TIME - timedelta(minutes=40)

    records, stats = build_kalshi_capture_records(
        [seattle, houston], candidates, {776001}, run_date=date(2026, 8, 15), now=now
    )

    assert stats["captured"] == 1
    assert len(records) == 1
    record = records[0]
    assert record["game_pk"] == 776001
    assert record["run_date"] == date(2026, 8, 15)
    assert record["bookmaker"] == "kalshi"
    assert record["source"] == "kalshi"
    # Houston (home, midpoint 0.55) is the favorite; Seattle (away, 0.45) the underdog.
    assert record["home_american"] == -122
    assert record["away_american"] == 122
    assert record["snapshot_timestamp"] == EVENT_TIME - timedelta(minutes=45)


def test_build_records_isolates_one_bad_market_row_from_others() -> None:
    bad = _market_row("T-BAD", "E-BAD", "", "Nowhere", yes_bid=0.5, yes_ask=0.5)
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]
    now = EVENT_TIME - timedelta(minutes=40)

    records, stats = build_kalshi_capture_records(
        [bad, seattle, houston], candidates, {776001}, run_date=date(2026, 8, 15), now=now
    )

    assert stats["match_errors"] == 1
    assert stats["captured"] == 1
    assert len(records) == 1
    assert records[0]["game_pk"] == 776001


def test_build_records_skips_matched_game_not_in_due_set() -> None:
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]

    records, stats = build_kalshi_capture_records(
        [seattle, houston],
        candidates,
        set(),
        run_date=date(2026, 8, 15),
        now=EVENT_TIME - timedelta(minutes=40),
    )

    assert records == []
    assert stats["skipped_not_due"] == 2


def test_build_records_skips_game_missing_one_side() -> None:
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]

    records, stats = build_kalshi_capture_records(
        [seattle],
        candidates,
        {776001},
        run_date=date(2026, 8, 15),
        now=EVENT_TIME - timedelta(minutes=40),
    )

    assert records == []
    assert stats["incomplete_sides"] == 1


def test_build_records_skips_no_liquidity_side() -> None:
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.0, yes_ask=0.0)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]

    records, stats = build_kalshi_capture_records(
        [seattle, houston],
        candidates,
        {776001},
        run_date=date(2026, 8, 15),
        now=EVENT_TIME - timedelta(minutes=40),
    )

    assert records == []
    assert stats["no_liquidity"] == 1


def test_build_records_skips_snapshot_not_before_first_pitch() -> None:
    # The script ran late (or a stale bronze row) -- the capture would be a
    # misleading "pregame" read for a game that already started.
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]
    now = EVENT_TIME + timedelta(minutes=5)

    records, stats = build_kalshi_capture_records(
        [seattle, houston], candidates, {776001}, run_date=date(2026, 8, 15), now=now
    )

    assert records == []
    assert stats["snapshot_not_before_first_pitch"] == 1


def test_build_records_isolates_one_bad_game_from_a_healthy_second_game() -> None:
    # Two due games in one call; the first's market is unmatchable (raises
    # inside match_kalshi_market), the second is healthy -- the second must
    # still be captured.
    broken = _market_row("T-X", "E-X", "Nowhereville", "Alsonowhere", yes_bid=0.5, yes_ask=0.55)
    seattle = _market_row("T-SEA", "E1", "Seattle", "Houston", yes_bid=0.44, yes_ask=0.46)
    houston = _market_row("T-HOU", "E1", "Houston", "Seattle", yes_bid=0.54, yes_ask=0.56)
    candidates = [_candidate(776001, "Houston Astros", "Seattle Mariners")]
    now = EVENT_TIME - timedelta(minutes=40)

    records, stats = build_kalshi_capture_records(
        [broken, seattle, houston],
        candidates,
        {776001, 999999},
        run_date=date(2026, 8, 15),
        now=now,
    )

    assert stats["unmatched_events.no_team_match"] == 1
    assert len(records) == 1
    assert records[0]["game_pk"] == 776001


def test_build_records_same_city_collision_is_ambiguous_through_the_real_capture_path() -> None:
    # DATA-023 P1 regression, exercised through THIS script's own
    # build_kalshi_capture_records (not by calling matching.py directly): two
    # distinct same-day games each involve a "Chicago" club and a "Los
    # Angeles" club (Cubs@Dodgers and White Sox@Angels). A bare
    # "Chicago"/"Los Angeles" Kalshi market loosely word-subset-matches both
    # candidate pairs, so it must resolve AMBIGUOUS -- never silently
    # attributed to either game via nearest-time tie-break.
    market_yes = _market_row("T-CHI", "E-CHI", "Chicago", "Los Angeles", yes_bid=0.44, yes_ask=0.46)
    market_no = _market_row("T-LA", "E-CHI", "Los Angeles", "Chicago", yes_bid=0.54, yes_ask=0.56)
    cubs_dodgers = _candidate(101, "Chicago Cubs", "Los Angeles Dodgers", EVENT_TIME)
    sox_angels = _candidate(102, "Chicago White Sox", "Los Angeles Angels", EVENT_TIME)
    now = EVENT_TIME - timedelta(minutes=40)

    records, stats = build_kalshi_capture_records(
        [market_yes, market_no],
        [cubs_dodgers, sox_angels],
        {101, 102},
        run_date=date(2026, 8, 15),
        now=now,
    )

    assert records == []
    assert stats["unmatched_events.ambiguous_team_identity"] == 2
    assert "captured" not in stats


# ---------------------------------------------------------------------------
# End-to-end: multi-game, multi-start-time fixture slate
# ---------------------------------------------------------------------------


def _write_schedule(database: Path, games: list[dict]) -> None:
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS silver.games (
                game_pk BIGINT,
                game_type VARCHAR,
                official_date DATE,
                abstract_game_state VARCHAR,
                game_date TIMESTAMP,
                source_game_json VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO silver.games VALUES (?, 'R', ?, 'Preview', ?, ?)",
            [
                (
                    game["game_pk"],
                    game["official_date"],
                    game["game_date"].replace(tzinfo=None),
                    json.dumps(game["source_game_json"]),
                )
                for game in games
            ],
        )


def _kalshi_market(
    ticker: str,
    event_ticker: str,
    yes_sub_title: str,
    no_sub_title: str,
    occurrence: datetime,
    updated: datetime,
    *,
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": f"{yes_sub_title} vs {no_sub_title} Winner?",
        "yes_sub_title": yes_sub_title,
        "no_sub_title": no_sub_title,
        "yes_bid_dollars": f"{yes_bid:.4f}",
        "yes_ask_dollars": f"{yes_ask:.4f}",
        "no_bid_dollars": f"{no_bid:.4f}",
        "no_ask_dollars": f"{no_ask:.4f}",
        "occurrence_datetime": occurrence.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_time": updated.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
    }


def test_main_end_to_end_multi_game_multi_start_time_slate(tmp_path: Path) -> None:
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch_a = now + timedelta(minutes=40)  # inside the due window right now
    first_pitch_b = now + timedelta(hours=4)  # a later, separate first pitch -- not due yet
    updated = now - timedelta(minutes=5)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_a,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            },
            {
                "game_pk": 776002,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_b,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "San Francisco Giants"}},
                        "away": {"team": {"name": "Colorado Rockies"}},
                    }
                },
            },
        ],
    )

    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch_a, updated,
                yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch_a, updated,
                yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
            ),
            _kalshi_market(
                "T-COL", "E-B", "Colorado", "San Francisco", first_pitch_b, updated,
                yes_bid=0.40, yes_ask=0.42, no_bid=0.58, no_ask=0.60,
            ),
            _kalshi_market(
                "T-SF", "E-B", "San Francisco", "Colorado", first_pitch_b, updated,
                yes_bid=0.58, yes_ask=0.60, no_bid=0.40, no_ask=0.42,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["game_pk"] == 776001
    assert rows[0]["bookmaker"] == "kalshi"
    assert rows[0]["source"] == "kalshi"
    assert datetime.fromisoformat(rows[0]["snapshot_timestamp"]) == updated

    # A later scheduler invocation (this script run again, hours later) picks
    # up the SECOND game once IT nears its own, later first pitch -- each
    # game gets its own capture time, not one shared slate-wide timestamp.
    later_now = now + timedelta(hours=3, minutes=25)  # ~35 min before game B's first pitch
    later_updated = later_now - timedelta(minutes=2)
    payload["markets"][2]["updated_time"] = later_updated.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    payload["markets"][3]["updated_time"] = later_updated.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", later_now.isoformat(),
        ]
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert {row["game_pk"] for row in rows} == {776001, 776002}
    by_pk = {row["game_pk"]: row for row in rows}
    assert by_pk[776001]["snapshot_timestamp"] != by_pk[776002]["snapshot_timestamp"]
    # The first game's row is untouched by the second run -- it is not
    # re-fetched/re-polled once already captured.
    assert datetime.fromisoformat(by_pk[776001]["snapshot_timestamp"]) == updated
    assert datetime.fromisoformat(by_pk[776002]["snapshot_timestamp"]) == later_updated


def test_main_mixed_batch_isolates_stale_snapshot_from_a_healthy_game(tmp_path: Path) -> None:
    # Two games due in the SAME invocation: one whose matched Kalshi markets
    # carry a snapshot timestamp AFTER its own first pitch (a stale/late
    # bronze row -- must not be displayed as a misleading pregame read), one
    # completely healthy. The bad game must be skipped with NO effect on the
    # healthy game's row and NO exception.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch_a = now + timedelta(minutes=40)
    first_pitch_b = now + timedelta(minutes=40)
    healthy_updated = now - timedelta(minutes=5)
    stale_updated = first_pitch_b + timedelta(minutes=10)  # after game B's own first pitch

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_a,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            },
            {
                "game_pk": 776002,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_b,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "San Francisco Giants"}},
                        "away": {"team": {"name": "Colorado Rockies"}},
                    }
                },
            },
        ],
    )

    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch_a, healthy_updated,
                yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch_a, healthy_updated,
                yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
            ),
            _kalshi_market(
                "T-COL", "E-B", "Colorado", "San Francisco", first_pitch_b, stale_updated,
                yes_bid=0.40, yes_ask=0.42, no_bid=0.58, no_ask=0.60,
            ),
            _kalshi_market(
                "T-SF", "E-B", "San Francisco", "Colorado", first_pitch_b, stale_updated,
                yes_bid=0.58, yes_ask=0.60, no_bid=0.40, no_ask=0.42,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert {row["game_pk"] for row in rows} == {776001}


def test_main_double_invocation_same_game_does_not_recapture(tmp_path: Path) -> None:
    # Requirement 4 (idempotency), end-to-end: run twice against the SAME due
    # game with the SAME `now`. The second invocation must not re-fetch/
    # re-write it -- it should not even reach the point of touching the
    # (changed) Kalshi payload again.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)
    updated = now - timedelta(minutes=5)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            }
        ],
    )

    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch, updated,
                yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch, updated,
                yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    args = [
        "--date", "2026-08-15",
        "--database", str(database),
        "--odds-books-output", str(odds_books),
        "--kalshi-json", str(kalshi_json),
        "--now", now.isoformat(),
    ]

    assert main(args) == 0
    first_rows = odds_books.read_text(encoding="utf-8").splitlines()
    assert len(first_rows) == 1

    # A later poll would see different (even conflicting) live prices; since
    # the game is already captured it must never reach the fetch/ingest path
    # at all on the second invocation.
    payload["markets"][0]["yes_bid_dollars"] = "0.10"
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    assert main(args) == 0
    second_rows = odds_books.read_text(encoding="utf-8").splitlines()
    assert second_rows == first_rows


def test_main_already_captured_game_with_changed_price_does_not_block_a_due_game(
    tmp_path: Path,
) -> None:
    # PIPE-006 regression: the real bug was never "one game re-run in
    # isolation" (games_due_for_capture already returns early for that case,
    # before the fetch/ingest path is ever reached -- see the double-
    # invocation test above). The real conflict fires when an ALREADY-
    # captured game's market reappears in the SAME freshly-fetched payload
    # as a STILL-due game, at the SAME market_ticker + snapshot_timestamp
    # (Bronze's primary key) but a DIFFERENT price -- Kalshi's full-series
    # payload includes every open market regardless of this script's own
    # due-window bookkeeping. Pre-fix, ingesting the whole payload raised
    # KalshiDataError from the immutability check, which _run_capture's own
    # `except KalshiDataError` swallows into `return 0` -- silently
    # abandoning the WHOLE run, including the due game that should have
    # succeeded. The fix must let the due game capture anyway.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch_a = now + timedelta(minutes=40)  # due right now
    first_pitch_b = now + timedelta(hours=4)  # due later, once B nears ITS first pitch
    updated_a = now - timedelta(minutes=5)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_a,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            },
            {
                "game_pk": 776002,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch_b,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "San Francisco Giants"}},
                        "away": {"team": {"name": "Colorado Rockies"}},
                    }
                },
            },
        ],
    )

    # Invocation 1: only game A is due; capture it normally.
    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch_a, updated_a,
                yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch_a, updated_a,
                yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )
    assert exit_code == 0
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert {row["game_pk"] for row in rows} == {776001}
    game_a_row_after_invocation_1 = rows[0]

    # Invocation 2: game A is already captured (excluded from `due`), game B
    # is now due. The refreshed Kalshi payload still lists game A's markets
    # (a real GET /markets response includes every open market, not just
    # this script's own due ones) at the SAME market_ticker + SAME
    # updated_time as invocation 1, but a DIFFERENT price -- exactly the
    # "prices move throughout the day" conflict trigger from the bug report.
    later_now = now + timedelta(hours=3, minutes=25)  # ~35 min before game B's first pitch
    later_updated = later_now - timedelta(minutes=2)
    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch_a, updated_a,
                yes_bid=0.10, yes_ask=0.12, no_bid=0.88, no_ask=0.90,  # price moved
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch_a, updated_a,
                yes_bid=0.88, yes_ask=0.90, no_bid=0.10, no_ask=0.12,  # price moved
            ),
            _kalshi_market(
                "T-COL", "E-B", "Colorado", "San Francisco", first_pitch_b, later_updated,
                yes_bid=0.40, yes_ask=0.42, no_bid=0.58, no_ask=0.60,
            ),
            _kalshi_market(
                "T-SF", "E-B", "San Francisco", "Colorado", first_pitch_b, later_updated,
                yes_bid=0.58, yes_ask=0.60, no_bid=0.40, no_ask=0.42,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", later_now.isoformat(),
        ]
    )

    # The run must succeed (no immutability conflict) AND actually capture
    # the due game -- pre-fix, both invocations return 0, but pre-fix the
    # second one silently captures NOTHING (the conflict aborts before any
    # write), so exit_code alone cannot distinguish fixed from broken here.
    assert exit_code == 0
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    by_pk = {row["game_pk"]: row for row in rows}
    assert set(by_pk) == {776001, 776002}
    assert datetime.fromisoformat(by_pk[776002]["snapshot_timestamp"]) == later_updated

    # Game A's already-captured row is byte-for-byte untouched -- not just
    # "some row survived," but the EXACT record from invocation 1, proving
    # the changed price in invocation 2's payload never got re-applied.
    assert by_pk[776001] == game_a_row_after_invocation_1
    assert datetime.fromisoformat(by_pk[776001]["snapshot_timestamp"]) == updated_a

    # And Bronze itself was never re-ingested with the conflicting price --
    # confirms the filter, not luck, is what avoided the conflict.
    with duckdb.connect(str(database), read_only=True) as connection:
        stored_yes_bid = connection.execute(
            "SELECT yes_bid FROM bronze.kalshi_market_snapshots WHERE market_ticker = 'T-SEA'"
        ).fetchone()[0]
    assert stored_yes_bid == Decimal("0.4400")


def test_main_empty_kalshi_markets_payload_is_a_normal_noop(tmp_path: Path) -> None:
    # An empty Kalshi API response (no markets currently listed) is a normal
    # empty result, not malformed data -- must not crash and must not write
    # any rows, even though a game is due.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            }
        ],
    )
    kalshi_json.write_text(json.dumps({"markets": []}), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0
    assert not odds_books.exists()
    with duckdb.connect(str(database), read_only=True) as connection:
        count = connection.execute(
            "SELECT count(*) FROM bronze.kalshi_market_snapshots"
        ).fetchone()[0]
    assert count == 0


def test_main_malformed_top_level_payload_leaves_no_bronze_table_and_no_trace(
    tmp_path: Path,
) -> None:
    # A structurally malformed Kalshi payload (e.g. no "markets" key at all --
    # a completely different response shape, not just one bad record) must be
    # rejected before any Bronze write, and the script must not crash. This
    # is the "never-ingested Bronze table for a given date" scenario: nothing
    # gets created at all, not a half-written table.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            }
        ],
    )
    kalshi_json.write_text(json.dumps({"cursor": "abc"}), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0
    assert not odds_books.exists()
    with duckdb.connect(str(database), read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
            ).fetchall()
        }
    assert "kalshi_market_snapshots" not in tables


def test_main_one_malformed_market_blocks_capture_for_every_other_due_game(
    tmp_path: Path,
) -> None:
    """Adversarial: one malformed, UNRELATED market in the same Kalshi
    payload must not prevent a completely healthy due game's capture.

    Per the task's own critical constraint: "a missing, unmatched, or errored
    Kalshi fetch for one game must not prevent ... any other game['s]" data
    from being captured. ``ingest_kalshi_market_snapshots`` parses the WHOLE
    payload before any DB write and raises ``KalshiDataError`` on the FIRST
    malformed market anywhere in ``payload["markets"]`` (pinned intentionally
    at the DATA-022 ingestion layer as fail-fast batch semantics for a single
    ingest call). ``_run_capture`` catches that and returns 0 for the WHOLE
    invocation, so a healthy, due game sharing the same Kalshi payload is
    silently skipped this run too -- not just the bad market's own game.

    This is expected to FAIL (not xfail-pinned: this is P1, not a low-
    severity/unreachable gap -- a single malformed record anywhere in a real
    Kalshi MLB slate response is a realistic occurrence, and as long as it
    persists across repeated polls it can block Kalshi capture for the ENTIRE
    slate, not just the game it names).
    """
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            }
        ],
    )

    healthy_a = _kalshi_market(
        "T-SEA", "E-A", "Seattle", "Houston", first_pitch, now - timedelta(minutes=5),
        yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
    )
    healthy_b = _kalshi_market(
        "T-HOU", "E-A", "Houston", "Seattle", first_pitch, now - timedelta(minutes=5),
        yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
    )
    malformed = _kalshi_market(
        "T-BAD", "E-BAD", "Nowhere", "Alsonowhere", first_pitch, now - timedelta(minutes=5),
        yes_bid=0.5, yes_ask=0.5, no_bid=0.5, no_ask=0.5,
    )
    del malformed["no_sub_title"]  # one malformed, otherwise-unrelated market

    kalshi_json.write_text(
        json.dumps({"markets": [healthy_a, healthy_b, malformed]}), encoding="utf-8"
    )

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0  # must never raise/crash -- this part holds
    assert odds_books.exists(), (
        "one malformed, unrelated Kalshi market blocked Bronze ingestion for "
        "the whole batch, so the healthy due game 776001 was never captured "
        "this invocation -- see P1 finding in the tester report"
    )
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert {row["game_pk"] for row in rows} == {776001}


def test_main_one_malformed_schedule_game_does_not_block_capture_for_other_games(
    tmp_path: Path,
) -> None:
    """Adversarial: one game's malformed ``source_game_json`` must not crash
    the whole invocation or block a completely unrelated healthy due game.

    ``kalshi_game_candidates_from_schedule`` raises ``KalshiMatchingError``
    for the WHOLE schedule (its own fail-fast loop over every game) if any
    single game's ``source_game_json`` is missing the expected team-name
    shape. Before this fix, ``_run_capture`` built ``candidates`` outside any
    try/except, so that exception propagated uncaught through ``main()`` and
    crashed the entire invocation with zero games captured -- same failure
    class as the market-level P1, at a different choke point.
    """
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    kalshi_json = tmp_path / "kalshi.json"

    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            },
            {
                # Malformed: missing the expected "teams" shape entirely.
                "game_pk": 776099,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {},
            },
        ],
    )

    payload = {
        "markets": [
            _kalshi_market(
                "T-SEA", "E-A", "Seattle", "Houston", first_pitch, now - timedelta(minutes=5),
                yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56,
            ),
            _kalshi_market(
                "T-HOU", "E-A", "Houston", "Seattle", first_pitch, now - timedelta(minutes=5),
                yes_bid=0.54, yes_ask=0.56, no_bid=0.44, no_ask=0.46,
            ),
        ]
    }
    kalshi_json.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--kalshi-json", str(kalshi_json),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0  # must never raise/crash
    assert odds_books.exists(), (
        "one game's malformed source_game_json blocked candidate-building "
        "for the WHOLE schedule, so the healthy due game 776001 was never "
        "captured this invocation"
    )
    rows = [json.loads(line) for line in odds_books.read_text(encoding="utf-8").splitlines()]
    assert {row["game_pk"] for row in rows} == {776001}


def test_main_missing_kalshi_fetch_never_raises_and_leaves_no_trace(tmp_path: Path, monkeypatch) -> None:
    # A total Kalshi API outage (network error) must never raise/crash --
    # comparison-only data, isolated from everything else this repo does.
    database = tmp_path / "mlb.duckdb"
    odds_books = tmp_path / "odds_books.jsonl"
    now = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    first_pitch = now + timedelta(minutes=40)

    _write_schedule(
        database,
        [
            {
                "game_pk": 776001,
                "official_date": date(2026, 8, 15),
                "game_date": first_pitch,
                "source_game_json": {
                    "teams": {
                        "home": {"team": {"name": "Houston Astros"}},
                        "away": {"team": {"name": "Seattle Mariners"}},
                    }
                },
            }
        ],
    )

    import scripts.kalshi_pregame_capture as kpc

    def _boom(**_kwargs):
        raise RuntimeError("Kalshi API request failed: timed out")

    monkeypatch.setattr(kpc, "fetch_kalshi_payload", _boom)

    exit_code = main(
        [
            "--date", "2026-08-15",
            "--database", str(database),
            "--odds-books-output", str(odds_books),
            "--now", now.isoformat(),
        ]
    )

    assert exit_code == 0
    assert not odds_books.exists()
