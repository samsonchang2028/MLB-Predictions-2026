import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingestion.mlb import ingest_schedule
from storage import connect_database, storage_paths


def _game(
    game_pk: int,
    *,
    game_number: int = 1,
    detail: str = "Scheduled",
    **extra: object,
) -> dict[str, object]:
    return {
        "gamePk": game_pk,
        "season": "2025",
        "gameType": "R",
        "gameDate": "2025-04-01T20:10:00Z",
        "officialDate": "2025-04-01",
        "doubleHeader": "Y",
        "gameNumber": game_number,
        "teams": {
            "away": {"team": {"id": 110}},
            "home": {"team": {"id": 137}},
        },
        "status": {
            "abstractGameState": "Preview",
            "codedGameState": "S",
            "detailedState": detail,
            "statusCode": "S",
        },
        **extra,
    }


def _payload(*games: dict[str, object]) -> bytes:
    return json.dumps({"dates": [{"date": "2025-04-01", "games": games}]}).encode()


def test_doubleheader_games_remain_distinct_and_repeat_ingestion_is_idempotent(
    tmp_path: Path,
) -> None:
    response = _payload(_game(1001), _game(1002, game_number=2))
    fetched_at = datetime(2025, 3, 1, 12, tzinfo=timezone.utc)

    first = ingest_schedule(
        tmp_path, lambda _: response, season=2025, fetched_at=fetched_at
    )
    second = ingest_schedule(
        tmp_path, lambda _: response, season=2025, fetched_at=fetched_at
    )

    assert first["raw_path"] == second["raw_path"]
    assert first["raw_path"].read_bytes() == response
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        games = connection.execute(
            "SELECT game_pk, game_number FROM bronze.mlb_games ORDER BY game_number"
        ).fetchall()
        canonical_count = connection.execute(
            "SELECT count(*) FROM bronze.mlb_games"
        ).fetchone()[0]
        observation_count = connection.execute(
            "SELECT count(*) FROM bronze.mlb_game_observations"
        ).fetchone()[0]
        payload_count = connection.execute(
            "SELECT count(*) FROM bronze.mlb_schedule_payloads"
        ).fetchone()[0]
        fetch_count = connection.execute(
            "SELECT count(*) FROM bronze.mlb_schedule_fetches"
        ).fetchone()[0]

    assert games == [(1001, 1), (1002, 2)]
    assert (canonical_count, observation_count, payload_count, fetch_count) == (2, 2, 1, 1)


def test_reschedule_status_history_is_preserved_while_canonical_game_is_updated(
    tmp_path: Path,
) -> None:
    postponed = _payload(
        _game(
            2001,
            detail="Postponed",
            rescheduleDate="2025-04-03",
            rescheduledFromDate="2025-04-01",
        )
    )
    rescheduled = _payload(
        {
            **_game(
                2001,
                detail="Scheduled",
                rescheduleDate="2025-04-03",
                rescheduledFromDate="2025-04-01",
                resumeDate="2025-04-03T20:10:00Z",
            ),
            "gameDate": "2025-04-03T16:10:00-04:00",
            "officialDate": "2025-04-03",
        }
    )

    ingest_schedule(
        tmp_path,
        lambda _: postponed,
        start_date="2025-04-01",
        end_date="2025-04-01",
        fetched_at=datetime(2025, 4, 1, 18, tzinfo=timezone.utc),
    )
    ingest_schedule(
        tmp_path,
        lambda _: rescheduled,
        start_date="2025-04-03",
        end_date="2025-04-03",
        fetched_at=datetime(2025, 4, 2, 18, tzinfo=timezone.utc),
    )

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        current = connection.execute(
            """
            SELECT detailed_state, official_date, game_date,
                   rescheduled_from_date, resume_date
            FROM bronze.mlb_games WHERE game_pk = 2001
            """
        ).fetchone()
        history = connection.execute(
            """
            SELECT detailed_state FROM bronze.mlb_game_observations
            WHERE game_pk = 2001 ORDER BY observed_at
            """
        ).fetchall()

    assert current == (
        "Scheduled",
        datetime(2025, 4, 3).date(),
        datetime(2025, 4, 3, 20, 10),
        "2025-04-01",
        "2025-04-03T20:10:00Z",
    )
    assert history == [("Postponed",), ("Scheduled",)]


def test_older_response_cannot_replace_newer_canonical_state(tmp_path: Path) -> None:
    older = _payload(_game(3001, detail="Postponed"))
    newer = _payload(_game(3001, detail="Final"))

    ingest_schedule(
        tmp_path,
        lambda _: newer,
        season=2025,
        fetched_at=datetime(2025, 4, 2, tzinfo=timezone.utc),
    )
    ingest_schedule(
        tmp_path,
        lambda _: older,
        season=2025,
        fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
    )

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        state = connection.execute(
            "SELECT detailed_state FROM bronze.mlb_games WHERE game_pk = 3001"
        ).fetchone()
        observation_count = connection.execute(
            "SELECT count(*) FROM bronze.mlb_game_observations WHERE game_pk = 3001"
        ).fetchone()[0]

    assert state == ("Final",)
    assert observation_count == 2


def test_equal_time_conflict_fails_without_partial_database_lineage(tmp_path: Path) -> None:
    timestamp = datetime(2025, 4, 1, 12, tzinfo=timezone.utc)
    scheduled = _payload(_game(4001, detail="Scheduled"))
    postponed = _payload(_game(4001, detail="Postponed"))
    ingest_schedule(tmp_path, lambda _: scheduled, season=2025, fetched_at=timestamp)

    try:
        ingest_schedule(tmp_path, lambda _: postponed, season=2025, fetched_at=timestamp)
    except ValueError as error:
        assert "conflicting observations for gamePk 4001" in str(error)
    else:
        raise AssertionError("equal-time conflicting state was accepted")

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        current = connection.execute(
            "SELECT detailed_state FROM bronze.mlb_games WHERE game_pk = 4001"
        ).fetchone()
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM bronze.mlb_schedule_payloads),
                (SELECT count(*) FROM bronze.mlb_schedule_fetches),
                (SELECT count(*) FROM bronze.mlb_game_observations)
            """
        ).fetchone()

    assert current == ("Scheduled",)
    assert counts == (1, 1, 1)


def test_historical_equal_time_conflict_rolls_back_after_canonical_advances(
    tmp_path: Path,
) -> None:
    first_time = datetime(2025, 4, 1, 12, tzinfo=timezone.utc)
    later_time = datetime(2025, 4, 2, 12, tzinfo=timezone.utc)
    scheduled = _payload(_game(4501, detail="Scheduled"))
    final = _payload(_game(4501, detail="Final"))
    postponed = _payload(_game(4501, detail="Postponed"))

    ingest_schedule(tmp_path, lambda _: scheduled, season=2025, fetched_at=first_time)
    ingest_schedule(tmp_path, lambda _: final, season=2025, fetched_at=later_time)

    try:
        rejected = ingest_schedule(
            tmp_path, lambda _: postponed, season=2025, fetched_at=first_time
        )
    except ValueError as error:
        assert "conflicting observations for gamePk 4501" in str(error)
    else:
        raise AssertionError(f"historical equal-time conflict was accepted: {rejected}")

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        canonical = connection.execute(
            """
            SELECT detailed_state, observed_at
            FROM bronze.mlb_games WHERE game_pk = 4501
            """
        ).fetchone()
        history = connection.execute(
            """
            SELECT detailed_state, observed_at
            FROM bronze.mlb_game_observations
            WHERE game_pk = 4501 ORDER BY observed_at
            """
        ).fetchall()
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM bronze.mlb_schedule_payloads),
                (SELECT count(*) FROM bronze.mlb_schedule_fetches),
                (SELECT count(*) FROM bronze.mlb_game_observations),
                (SELECT count(*) FROM bronze.mlb_games)
            """
        ).fetchone()

    assert canonical == ("Final", later_time.replace(tzinfo=None))
    assert history == [
        ("Scheduled", first_time.replace(tzinfo=None)),
        ("Final", later_time.replace(tzinfo=None)),
    ]
    assert counts == (2, 2, 2, 1)
    assert len(list((tmp_path / "raw" / "mlb" / "schedules").rglob("*.json"))) == 3


def test_equal_time_identical_games_choose_payload_lineage_independent_of_order(
    tmp_path: Path,
) -> None:
    game = _game(4751)
    first_payload = json.dumps({"source": "a", "dates": [{"games": [game]}]}).encode()
    second_payload = json.dumps({"source": "b", "dates": [{"games": [game]}]}).encode()
    timestamp = datetime(2025, 4, 1, 12, tzinfo=timezone.utc)
    canonical_results = []

    for root, payloads in (
        (tmp_path / "forward", (first_payload, second_payload)),
        (tmp_path / "reverse", (second_payload, first_payload)),
    ):
        hashes = []
        for payload in payloads:
            result = ingest_schedule(
                root, lambda _, content=payload: content, season=2025, fetched_at=timestamp
            )
            hashes.append(result["payload_sha256"])

        with connect_database(storage_paths(root)["database"]) as connection:
            canonical = connection.execute(
                """
                SELECT payload_sha256, detailed_state, observed_at
                FROM bronze.mlb_games WHERE game_pk = 4751
                """
            ).fetchone()
            counts = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM bronze.mlb_schedule_payloads),
                    (SELECT count(*) FROM bronze.mlb_schedule_fetches),
                    (SELECT count(*) FROM bronze.mlb_game_observations)
                """
            ).fetchone()

        assert canonical[0] == min(hashes)
        assert counts == (2, 2, 2)
        canonical_results.append(canonical)

    assert canonical_results[0] == canonical_results[1]


def test_identical_content_preserves_each_fetch_time_and_latest_canonical_time(
    tmp_path: Path,
) -> None:
    response = _payload(_game(5001))
    earlier = datetime(
        2025, 3, 1, 8, tzinfo=timezone(timedelta(hours=-5))
    )
    later = datetime(2025, 3, 2, 9, tzinfo=timezone.utc)

    for root, timestamps in (
        (tmp_path / "chronological", (earlier, later, later)),
        (tmp_path / "reverse", (later, earlier, earlier)),
    ):
        for fetched_at in timestamps:
            ingest_schedule(
                root,
                lambda _: response,
                season=2025,
                fetched_at=fetched_at,
            )

        with connect_database(storage_paths(root)["database"]) as connection:
            fetch_times = connection.execute(
                "SELECT fetched_at FROM bronze.mlb_schedule_fetches ORDER BY fetched_at"
            ).fetchall()
            observation_times = connection.execute(
                """
                SELECT observed_at FROM bronze.mlb_game_observations
                WHERE game_pk = 5001 ORDER BY observed_at
                """
            ).fetchall()
            canonical_time = connection.execute(
                "SELECT observed_at FROM bronze.mlb_games WHERE game_pk = 5001"
            ).fetchone()
            payload_count = connection.execute(
                "SELECT count(*) FROM bronze.mlb_schedule_payloads"
            ).fetchone()[0]

        expected = [(datetime(2025, 3, 1, 13),), (later.replace(tzinfo=None),)]
        assert fetch_times == expected
        assert observation_times == expected
        assert canonical_time == (later.replace(tzinfo=None),)
        assert payload_count == 1
        assert all(value[0].tzinfo is None for value in fetch_times)
