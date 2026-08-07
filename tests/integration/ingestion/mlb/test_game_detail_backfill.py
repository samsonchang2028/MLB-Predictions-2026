import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingestion.mlb import backfill_game_details, ingest_schedule
from storage import connect_database, storage_paths


def _schedule_game(
    game_pk: int,
    *,
    detail: str = "Final",
    game_number: int = 1,
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
            "abstractGameState": "Final" if detail == "Final" else "Preview",
            "codedGameState": "F" if detail == "Final" else "S",
            "detailedState": detail,
            "statusCode": "F" if detail == "Final" else "S",
        },
    }


def _schedule_payload(*games: dict[str, object]) -> bytes:
    return json.dumps({"dates": [{"date": "2025-04-01", "games": games}]}).encode()


def _detail_payload(game_pk: int) -> bytes:
    return json.dumps(
        {
            "gamePk": game_pk,
            "gameData": {"probablePitchers": {}},
            "liveData": {
                "boxscore": {
                    "teams": {
                        "home": {"team": {"id": 137}, "players": {}, "pitchers": []},
                        "away": {"team": {"id": 110}, "players": {}, "pitchers": []},
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _ingest_games(root: Path, *games: dict[str, object]) -> None:
    ingest_schedule(
        root,
        lambda _: _schedule_payload(*games),
        season=2025,
        fetched_at=datetime(2025, 4, 2, tzinfo=timezone.utc),
    )


def test_restart_skips_terminal_rows_and_retry_resolves_missing_and_failed_games(
    tmp_path: Path,
) -> None:
    games = (
        _schedule_game(1001, game_number=1),
        _schedule_game(1002, detail="Suspended", game_number=2),
        _schedule_game(1003, detail="Postponed"),
        _schedule_game(1004, detail="Cancelled"),
        _schedule_game(1005, detail="Rescheduled"),
    )
    _ingest_games(tmp_path, *games)
    calls: list[tuple[str, dict[str, object]]] = []

    def first_fetch(endpoint: str, parameters: object) -> bytes | None:
        calls.append((endpoint, dict(parameters)))
        game_pk = int(endpoint.split("/")[4])
        if game_pk == 1003:
            return None
        if game_pk == 1004:
            raise TimeoutError("upstream timed out")
        return _detail_payload(game_pk)

    first = backfill_game_details(
        tmp_path,
        first_fetch,
        seasons=[2025],
        fetched_at=datetime(2025, 4, 3, tzinfo=timezone.utc),
        run_id="initial",
        build_id="build-a",
    )
    restart_calls: list[int] = []
    second = backfill_game_details(
        tmp_path,
        lambda *_: restart_calls.append(1) or b"not called",
        seasons=[2025],
        fetched_at=datetime(2025, 4, 4, tzinfo=timezone.utc),
        run_id="restart",
    )

    assert first["fetched"] == 3
    assert first["missing_game_pks"] == [1003]
    assert first["failed_game_pks"] == [1004]
    assert second["skipped_fetched"] == 3
    assert second["skipped_unresolved"] == 2
    assert restart_calls == []
    assert {call[0] for call in calls} == {
        "/api/v1.1/game/1001/feed/live",
        "/api/v1.1/game/1002/feed/live",
        "/api/v1.1/game/1003/feed/live",
        "/api/v1.1/game/1004/feed/live",
        "/api/v1.1/game/1005/feed/live",
    }
    assert all("fields" in call[1] and "plays" not in call[1]["fields"] for call in calls)

    retried = backfill_game_details(
        tmp_path,
        lambda endpoint, _: _detail_payload(int(endpoint.split("/")[4])),
        game_pks=[1004, 1003],
        retry_unresolved=True,
        fetched_at=datetime(2025, 4, 5, tzinfo=timezone.utc),
        run_id="retry",
        build_id="build-b",
    )

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        statuses = connection.execute(
            "SELECT game_pk, status FROM bronze.mlb_game_detail_status ORDER BY game_pk"
        ).fetchall()
        attempts = connection.execute(
            """SELECT game_pk, status, ingestion_run_id, ingestion_build_id
               FROM bronze.mlb_game_detail_attempts
               ORDER BY game_pk, attempted_at"""
        ).fetchall()
        payloads = connection.execute(
            """SELECT game_pk, source, endpoint, request_json, ingestion_run_id
               FROM bronze.mlb_game_detail_payloads ORDER BY game_pk"""
        ).fetchall()

    assert retried["fetched"] == 2
    assert statuses == [
        (1001, "fetched"),
        (1002, "fetched"),
        (1003, "fetched"),
        (1004, "fetched"),
        (1005, "fetched"),
    ]
    assert attempts == [
        (1001, "fetched", "initial", "build-a"),
        (1002, "fetched", "initial", "build-a"),
        (1003, "missing", "initial", "build-a"),
        (1003, "fetched", "retry", "build-b"),
        (1004, "failed", "initial", "build-a"),
        (1004, "fetched", "retry", "build-b"),
        (1005, "fetched", "initial", "build-a"),
    ]
    assert [row[0] for row in payloads] == [1001, 1002, 1003, 1004, 1005]
    assert all(row[1] == "MLB Stats API" for row in payloads)
    assert json.loads(payloads[0][3])["fields"]


def test_valid_raw_payload_is_never_refetched_or_silently_overwritten(
    tmp_path: Path,
) -> None:
    _ingest_games(tmp_path, _schedule_game(2001))
    original = _detail_payload(2001)
    backfill_game_details(
        tmp_path,
        lambda *_: original,
        game_pks=[2001],
        fetched_at=datetime(2025, 4, 3, tzinfo=timezone.utc),
        run_id="first",
    )
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        payload_sha, raw_path = connection.execute(
            """SELECT payload_sha256, raw_path
               FROM bronze.mlb_game_detail_payloads WHERE game_pk = 2001"""
        ).fetchone()
    stored_path = tmp_path / "raw" / raw_path
    called = False

    def changed_fetch(*_: object) -> bytes:
        nonlocal called
        called = True
        return _detail_payload(2001) + b"\n"

    repeated = backfill_game_details(
        tmp_path,
        changed_fetch,
        game_pks=[2001],
        retry_unresolved=True,
        fetched_at=datetime(2025, 4, 4, tzinfo=timezone.utc),
        run_id="second",
    )

    assert repeated["skipped_fetched"] == 1
    assert not called
    assert stored_path.read_bytes() == original
    assert len(list((tmp_path / "raw" / "mlb" / "game-details").rglob("*.json"))) == 1

    stored_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        backfill_game_details(
            tmp_path,
            changed_fetch,
            game_pks=[2001],
            run_id="third",
        )
    assert not called
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        retained_sha, payload_count, attempt_count = connection.execute(
            """SELECT
                   (SELECT payload_sha256 FROM bronze.mlb_game_detail_payloads
                    WHERE game_pk = 2001),
                   (SELECT count(*) FROM bronze.mlb_game_detail_payloads),
                   (SELECT count(*) FROM bronze.mlb_game_detail_attempts)
               """
        ).fetchone()
    assert retained_sha == payload_sha
    assert (payload_count, attempt_count) == (1, 1)


def test_retry_attempts_preserve_timezone_normalized_retrieval_order(tmp_path: Path) -> None:
    _ingest_games(tmp_path, _schedule_game(3001))
    first_time = datetime(2025, 4, 3, 8, tzinfo=timezone(timedelta(hours=-4)))
    second_time = datetime(2025, 4, 4, 12, tzinfo=timezone.utc)
    backfill_game_details(
        tmp_path, lambda *_: None, game_pks=[3001], fetched_at=first_time, run_id="a"
    )
    backfill_game_details(
        tmp_path,
        lambda *_: _detail_payload(3001),
        game_pks=[3001],
        retry_unresolved=True,
        fetched_at=second_time,
        run_id="b",
    )

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        times = connection.execute(
            """SELECT attempted_at FROM bronze.mlb_game_detail_attempts
               WHERE game_pk = 3001 ORDER BY attempted_at"""
        ).fetchall()
    assert times == [(datetime(2025, 4, 3, 12),), (datetime(2025, 4, 4, 12),)]
