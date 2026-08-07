import json
from datetime import datetime, timezone
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

    assert games == [(1001, 1), (1002, 2)]
    assert (canonical_count, observation_count, payload_count) == (2, 2, 1)


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
            "gameDate": "2025-04-03T20:10:00Z",
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
            SELECT detailed_state, official_date, rescheduled_from_date, resume_date
            FROM bronze.mlb_games WHERE game_pk = 2001
            """
        ).fetchone()
        history = connection.execute(
            """
            SELECT detailed_state FROM bronze.mlb_game_observations
            WHERE game_pk = 2001 ORDER BY observed_at
            """
        ).fetchall()

    assert current == ("Scheduled", datetime(2025, 4, 3).date(), "2025-04-01", "2025-04-03T20:10:00Z")
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
