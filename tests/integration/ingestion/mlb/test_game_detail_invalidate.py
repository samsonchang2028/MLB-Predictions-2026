"""DATA-018: invalidate stale hollow ``bronze.mlb_game_detail_payloads`` rows
so a re-run of ``backfill_game_details`` genuinely re-fetches instead of
silently skipping already-payload'd games.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingestion.mlb import backfill_game_details, ingest_schedule
from ingestion.mlb.game_detail import (
    _create_tables,
    _retain_raw_payload,
    _store_success,
    invalidate_game_detail_payloads,
)
from storage import connect_database, initialize_storage, storage_paths


def _schedule_game(game_pk: int) -> dict[str, object]:
    return {
        "gamePk": game_pk,
        "season": "2025",
        "gameType": "R",
        "gameDate": "2025-04-01T20:10:00Z",
        "officialDate": "2025-04-01",
        "doubleHeader": "N",
        "gameNumber": 1,
        "teams": {
            "away": {"team": {"id": 110}},
            "home": {"team": {"id": 137}},
        },
        "status": {
            "abstractGameState": "Final",
            "codedGameState": "F",
            "detailedState": "Final",
            "statusCode": "F",
        },
    }


def _real_pitching() -> dict[str, object]:
    return {
        "inningsPitched": "6.0", "outs": 18, "battersFaced": 25,
        "numberOfPitches": 97, "hits": 6, "runs": 3, "earnedRuns": 3,
        "baseOnBalls": 3, "strikeOuts": 3, "homeRuns": 1,
    }


def _detail_payload(game_pk: int, *, pitching: dict[str, object] | None) -> bytes:
    """Build a boxscore payload. ``pitching=None`` reproduces the DATA-016
    hollow shape: ``person`` populated, ``stats``/``pitching`` empty."""
    home_stats = {"pitching": pitching} if pitching is not None else {}
    away_stats = {"pitching": pitching} if pitching is not None else {}
    return json.dumps(
        {
            "gamePk": game_pk,
            "liveData": {
                "boxscore": {
                    "teams": {
                        "home": {
                            "team": {"id": 137}, "pitchers": [1],
                            "players": {
                                "ID1": {
                                    "person": {"id": 1, "fullName": "Home Starter"},
                                    "stats": home_stats,
                                }
                            },
                        },
                        "away": {
                            "team": {"id": 110}, "pitchers": [2],
                            "players": {
                                "ID2": {
                                    "person": {"id": 2, "fullName": "Away Starter"},
                                    "stats": away_stats,
                                }
                            },
                        },
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _ingest_games(root: Path, *game_pks: int) -> None:
    games = [_schedule_game(pk) for pk in game_pks]
    payload = json.dumps({"dates": [{"date": "2025-04-01", "games": games}]}).encode()
    ingest_schedule(
        root, lambda _: payload, season=2025,
        fetched_at=datetime(2025, 4, 2, tzinfo=timezone.utc),
    )


def _seed_hollow_payload(
    root: Path, game_pk: int, *, run_id: str = "old-run", build_id: str = "DATA-005"
) -> tuple[Path, bytes]:
    """Directly store a pre-DATA-016-shaped hollow payload, bypassing the
    current (DATA-016) fetch-time hollow-payload guard -- exactly reproducing
    how the real 14,520-game 2021-2025 build already sits in production."""
    paths = initialize_storage(root)
    with connect_database(paths["database"]) as connection:
        _create_tables(connection)
    hollow_bytes = _detail_payload(game_pk, pitching=None)
    payload_sha = hashlib.sha256(hollow_bytes).hexdigest()
    relative_path = (
        Path("mlb") / "game-details" / str(game_pk) / payload_sha[:2]
        / f"{payload_sha}.json"
    )
    raw_path = _retain_raw_payload(root, relative_path, hollow_bytes)
    _store_success(
        paths["database"], game_pk, f"/api/v1.1/game/{game_pk}/feed/live",
        {"fields": "old-projection"}, datetime(2024, 1, 1), run_id, build_id,
        payload_sha, relative_path, hollow_bytes.decode(),
    )
    return raw_path, hollow_bytes


def test_invalidate_removes_only_targeted_rows_and_preserves_everything_else(
    tmp_path: Path,
) -> None:
    _ingest_games(tmp_path, 6001, 6002, 6003)
    raw_1, bytes_1 = _seed_hollow_payload(tmp_path, 6001, run_id="run-a")
    raw_2, bytes_2 = _seed_hollow_payload(tmp_path, 6002, run_id="run-a")
    raw_3, bytes_3 = _seed_hollow_payload(tmp_path, 6003, run_id="run-a")

    database = storage_paths(tmp_path)["database"]
    with connect_database(database) as connection:
        attempts_before = connection.execute(
            "SELECT game_pk, status FROM bronze.mlb_game_detail_attempts ORDER BY game_pk"
        ).fetchall()

    deleted = invalidate_game_detail_payloads(tmp_path, [6001, 6002])

    assert deleted == 2
    with connect_database(database) as connection:
        remaining = connection.execute(
            "SELECT game_pk FROM bronze.mlb_game_detail_payloads ORDER BY game_pk"
        ).fetchall()
        attempts_after = connection.execute(
            "SELECT game_pk, status FROM bronze.mlb_game_detail_attempts ORDER BY game_pk"
        ).fetchall()

    assert remaining == [(6003,)]
    # The attempts audit trail is untouched by invalidation.
    assert attempts_after == attempts_before

    # No raw file is deleted or mutated, including the two invalidated ones.
    assert raw_1.read_bytes() == bytes_1
    assert raw_2.read_bytes() == bytes_2
    assert raw_3.read_bytes() == bytes_3


@pytest.mark.parametrize(
    "game_pks",
    [
        [],
        [0],
        [-1],
        [True],
        [7001, 7001],
    ],
)
def test_invalidate_raises_on_invalid_input(tmp_path: Path, game_pks: list) -> None:
    with pytest.raises(ValueError):
        invalidate_game_detail_payloads(tmp_path, game_pks)


def test_invalidate_then_retry_unresolved_genuinely_refetches_hollow_game(
    tmp_path: Path,
) -> None:
    _ingest_games(tmp_path, 8001)
    raw_path, hollow_bytes = _seed_hollow_payload(tmp_path, 8001)

    def real_fetcher(endpoint: str, _: object) -> bytes:
        game_pk = int(endpoint.split("/")[4])
        return _detail_payload(game_pk, pitching=_real_pitching())

    deleted = invalidate_game_detail_payloads(tmp_path, [8001])
    assert deleted == 1

    result = backfill_game_details(
        tmp_path, real_fetcher, game_pks=[8001], retry_unresolved=True,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc), run_id="reingest",
    )

    assert result["fetched"] == 1
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        stored_json, stored_run = connection.execute(
            """SELECT payload_json, ingestion_run_id
               FROM bronze.mlb_game_detail_payloads WHERE game_pk = 8001"""
        ).fetchone()

    assert stored_run == "reingest"
    stored = json.loads(stored_json)
    pitching = stored["liveData"]["boxscore"]["teams"]["home"]["players"]["ID1"]["stats"]["pitching"]
    assert pitching["earnedRuns"] == 3  # real stats, contrasted with the old hollow {}

    # The original hollow raw file is untouched on disk, unreferenced but never
    # deleted (raw-payload immutability).
    assert raw_path.read_bytes() == hollow_bytes


def test_without_invalidation_retry_unresolved_still_skips_already_payloaded_games(
    tmp_path: Path,
) -> None:
    # Regression: proves the pre-DATA-018 no-op failure mode is unchanged when
    # invalidation is not explicitly requested -- invalidation is opt-in.
    _ingest_games(tmp_path, 9001)
    _seed_hollow_payload(tmp_path, 9001)
    called = False

    def real_fetcher(endpoint: str, _: object) -> bytes:
        nonlocal called
        called = True
        return _detail_payload(9001, pitching=_real_pitching())

    result = backfill_game_details(
        tmp_path, real_fetcher, game_pks=[9001], retry_unresolved=True,
        run_id="no-invalidate",
    )

    assert result["skipped_fetched"] == 1
    assert result["fetched"] == 0
    assert not called
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        stored_json = connection.execute(
            "SELECT payload_json FROM bronze.mlb_game_detail_payloads WHERE game_pk = 9001"
        ).fetchone()[0]
    stored = json.loads(stored_json)
    assert stored["liveData"]["boxscore"]["teams"]["home"]["players"]["ID1"]["stats"] == {}
