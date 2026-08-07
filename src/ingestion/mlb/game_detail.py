"""Backfill immutable MLB game-detail payloads by ``game_pk``."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from storage import connect_database, initialize_storage, write_raw_payload

GameDetailFetcher = Callable[[str, Mapping[str, object]], bytes | None]

SOURCE = "MLB Stats API"
ENDPOINT_TEMPLATE = "/api/v1.1/game/{game_pk}/feed/live"
DEFAULT_SEASONS = (2021, 2022, 2023, 2024, 2025)
BUILD_ID = "DATA-005"

# One filtered live-feed request provides probable starters and boxscore data
# without retaining play-by-play or pitch-level content. The boxscore ``players``
# object is keyed by dynamic ids (e.g. "ID660271") that cannot be enumerated in a
# static allowlist; listing it makes the live feed return ``players`` empty, which
# then breaks silver pitcher-stat extraction. So ``players`` and its sub-keys are
# deliberately omitted here and the full players subtree is returned unfiltered.
GAME_DETAIL_FIELDS = ",".join(
    (
        "gamePk", "gameData", "probablePitchers", "away", "home", "id",
        "fullName", "liveData", "boxscore", "teams", "team", "pitchers",
    )
)


def backfill_game_details(
    storage_root: str | Path,
    fetch_game_detail: GameDetailFetcher,
    *,
    game_pks: Iterable[int] | None = None,
    seasons: Iterable[int] = DEFAULT_SEASONS,
    retry_unresolved: bool = False,
    fetched_at: datetime | None = None,
    run_id: str | None = None,
    build_id: str = BUILD_ID,
) -> dict[str, object]:
    """Fetch pending details and retain immutable payload and attempt history.

    The fetcher receives an endpoint path and request parameters. It returns
    exact response bytes, or ``None`` for an upstream not-found result.
    Previously fetched games are always skipped after their raw bytes are hash
    checked. Missing and failed games are retried only when requested.
    """
    if not isinstance(retry_unresolved, bool):
        raise TypeError("retry_unresolved must be boolean")
    run_identity = _identity(run_id or str(uuid4()), "run_id")
    build_identity = _identity(build_id, "build_id")
    fixed_fetched_at = _utc_timestamp(fetched_at) if fetched_at is not None else None

    paths = initialize_storage(storage_root)
    with connect_database(paths["database"]) as connection:
        _create_tables(connection)
        targets = _target_game_pks(connection, game_pks, seasons)

    result: dict[str, object] = {
        "run_id": run_identity,
        "targeted": len(targets),
        "fetched": 0,
        "missing": 0,
        "failed": 0,
        "skipped_fetched": 0,
        "skipped_unresolved": 0,
        "missing_game_pks": [],
        "failed_game_pks": [],
    }
    request = {"fields": GAME_DETAIL_FIELDS}

    for game_pk in targets:
        endpoint = ENDPOINT_TEMPLATE.format(game_pk=game_pk)
        with connect_database(paths["database"]) as connection:
            existing = connection.execute(
                """SELECT payload_sha256, raw_path
                   FROM bronze.mlb_game_detail_payloads WHERE game_pk = ?""",
                [game_pk],
            ).fetchone()
            if existing is not None:
                try:
                    _verify_raw_payload(storage_root, existing[0], existing[1])
                except RuntimeError as error:
                    # Isolate the corrupt game_pk instead of aborting the whole
                    # backfill: record a retryable failure (never accept the
                    # tampered payload as valid) and keep processing other games.
                    attempted_at = (
                        fixed_fetched_at
                        or _utc_timestamp(datetime.now(timezone.utc))
                    )
                    _record_attempt(
                        paths["database"], game_pk, endpoint, request, attempted_at,
                        run_identity, build_identity, "failed",
                        error_type=type(error).__name__, error_message=str(error),
                    )
                    result["failed"] += 1
                    result["failed_game_pks"].append(game_pk)
                    continue
                result["skipped_fetched"] += 1
                continue
            prior_status = connection.execute(
                """SELECT status FROM bronze.mlb_game_detail_attempts
                   WHERE game_pk = ?
                   ORDER BY attempted_at DESC, ingestion_run_id DESC LIMIT 1""",
                [game_pk],
            ).fetchone()
        if prior_status is not None and not retry_unresolved:
            result["skipped_unresolved"] += 1
            continue

        try:
            payload = fetch_game_detail(endpoint, request)
        except Exception as error:
            attempted_at = fixed_fetched_at or _utc_timestamp(datetime.now(timezone.utc))
            _record_attempt(
                paths["database"], game_pk, endpoint, request, attempted_at,
                run_identity, build_identity, "failed",
                error_type=type(error).__name__, error_message=str(error),
            )
            result["failed"] += 1
            result["failed_game_pks"].append(game_pk)
            continue

        if payload is None:
            attempted_at = fixed_fetched_at or _utc_timestamp(datetime.now(timezone.utc))
            _record_attempt(
                paths["database"], game_pk, endpoint, request, attempted_at,
                run_identity, build_identity, "missing",
                error_type="not_found", error_message="fetcher returned no payload",
            )
            result["missing"] += 1
            result["missing_game_pks"].append(game_pk)
            continue

        try:
            payload_text = _validate_payload(payload, game_pk)
        except (TypeError, ValueError) as error:
            attempted_at = fixed_fetched_at or _utc_timestamp(datetime.now(timezone.utc))
            _record_attempt(
                paths["database"], game_pk, endpoint, request, attempted_at,
                run_identity, build_identity, "failed",
                error_type=type(error).__name__, error_message=str(error),
            )
            result["failed"] += 1
            result["failed_game_pks"].append(game_pk)
            continue

        payload_sha = hashlib.sha256(payload).hexdigest()
        retrieved_at = fixed_fetched_at or _utc_timestamp(datetime.now(timezone.utc))
        relative_path = (
            Path("mlb") / "game-details" / str(game_pk) / payload_sha[:2]
            / f"{payload_sha}.json"
        )
        _retain_raw_payload(storage_root, relative_path, payload)
        _store_success(
            paths["database"], game_pk, endpoint, request, retrieved_at,
            run_identity, build_identity, payload_sha, relative_path, payload_text,
        )
        result["fetched"] += 1

    return result


def _target_game_pks(
    connection: Any,
    game_pks: Iterable[int] | None,
    seasons: Iterable[int],
) -> list[int]:
    if game_pks is not None:
        requested = _positive_ints(game_pks, "game_pks")
        if not requested:
            return []
        placeholders = ", ".join("?" for _ in requested)
        found = {
            row[0]
            for row in connection.execute(
                f"SELECT game_pk FROM bronze.mlb_games WHERE game_pk IN ({placeholders})",
                requested,
            ).fetchall()
        }
        missing = sorted(set(requested) - found)
        if missing:
            raise ValueError(f"game_pks are absent from bronze.mlb_games: {missing}")
        return requested

    requested_seasons = _positive_ints(seasons, "seasons")
    if not requested_seasons:
        raise ValueError("seasons must not be empty")
    if any(season not in DEFAULT_SEASONS for season in requested_seasons):
        raise ValueError("seasons must be within the DATA-005 2021-2025 scope")
    placeholders = ", ".join("?" for _ in requested_seasons)
    return [
        row[0]
        for row in connection.execute(
            f"""SELECT game_pk FROM bronze.mlb_games
                WHERE try_cast(season AS INTEGER) IN ({placeholders})
                ORDER BY game_pk""",
            requested_seasons,
        ).fetchall()
    ]


def _positive_ints(values: Iterable[int], name: str) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must contain positive integers")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result)


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fetched_at must include a timezone")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_payload(payload: object, expected_game_pk: int) -> str:
    if not isinstance(payload, bytes):
        raise TypeError("fetch_game_detail must return exact bytes or None")
    try:
        text = payload.decode("utf-8")
        response = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MLB game-detail response is not valid JSON") from error
    if not isinstance(response, dict):
        raise ValueError("MLB game-detail response must be an object")
    game_pk = response.get("gamePk")
    if game_pk != expected_game_pk or isinstance(game_pk, bool):
        raise ValueError(
            f"game-detail gamePk {game_pk!r} does not match requested {expected_game_pk}"
        )
    return text


def _retain_raw_payload(root: str | Path, relative_path: Path, payload: bytes) -> Path:
    try:
        return write_raw_payload(root, relative_path, payload)
    except FileExistsError:
        existing = Path(root) / "raw" / relative_path
        if existing.read_bytes() != payload:
            raise RuntimeError("raw game-detail payload hash collision")
        return existing


def _verify_raw_payload(
    root: str | Path, expected_sha256: str, relative_path: str
) -> None:
    raw_root = (Path(root) / "raw").resolve()
    raw_path = (raw_root / relative_path).resolve()
    if not raw_path.is_relative_to(raw_root) or not raw_path.is_file():
        raise RuntimeError(f"stored raw game-detail payload is missing: {relative_path}")
    actual_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha256:
        raise RuntimeError(
            f"stored raw game-detail payload hash mismatch for {relative_path}"
        )


def _record_attempt(
    database_path: str | Path,
    game_pk: int,
    endpoint: str,
    request: Mapping[str, object],
    attempted_at: datetime,
    run_id: str,
    build_id: str,
    status: str,
    *,
    payload_sha256: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    with connect_database(database_path) as connection:
        # (ingestion_run_id, game_pk) holds the latest attempt within a run.
        # Re-attempting the same game under a reused run_id (restart/retry)
        # updates that row in place instead of raising a PK conflict.
        connection.execute(
            """INSERT INTO bronze.mlb_game_detail_attempts VALUES (
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               )
               ON CONFLICT (ingestion_run_id, game_pk) DO UPDATE SET
                   source = EXCLUDED.source,
                   endpoint = EXCLUDED.endpoint,
                   request_json = EXCLUDED.request_json,
                   attempted_at = EXCLUDED.attempted_at,
                   ingestion_build_id = EXCLUDED.ingestion_build_id,
                   status = EXCLUDED.status,
                   payload_sha256 = EXCLUDED.payload_sha256,
                   error_type = EXCLUDED.error_type,
                   error_message = EXCLUDED.error_message""",
            [
                run_id, game_pk, SOURCE, endpoint,
                json.dumps(request, sort_keys=True, separators=(",", ":")),
                attempted_at, build_id, status, payload_sha256,
                error_type, error_message,
            ],
        )


def _store_success(
    database_path: str | Path,
    game_pk: int,
    endpoint: str,
    request: Mapping[str, object],
    retrieved_at: datetime,
    run_id: str,
    build_id: str,
    payload_sha256: str,
    relative_path: Path,
    payload_json: str,
) -> None:
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    with connect_database(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            existing = connection.execute(
                """SELECT payload_sha256 FROM bronze.mlb_game_detail_payloads
                   WHERE game_pk = ?""",
                [game_pk],
            ).fetchone()
            if existing is not None and existing[0] != payload_sha256:
                raise RuntimeError(
                    f"refusing to replace existing game-detail payload for game_pk {game_pk}"
                )
            connection.execute(
                """INSERT INTO bronze.mlb_game_detail_payloads VALUES (
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                   ) ON CONFLICT DO NOTHING""",
                [
                    game_pk, SOURCE, endpoint, request_json, retrieved_at,
                    payload_sha256, relative_path.as_posix(), run_id, build_id,
                    payload_json,
                ],
            )
            connection.execute(
                """INSERT INTO bronze.mlb_game_detail_attempts VALUES (
                       ?, ?, ?, ?, ?, ?, ?, 'fetched', ?, NULL, NULL
                   )
                   ON CONFLICT (ingestion_run_id, game_pk) DO UPDATE SET
                       source = EXCLUDED.source,
                       endpoint = EXCLUDED.endpoint,
                       request_json = EXCLUDED.request_json,
                       attempted_at = EXCLUDED.attempted_at,
                       ingestion_build_id = EXCLUDED.ingestion_build_id,
                       status = EXCLUDED.status,
                       payload_sha256 = EXCLUDED.payload_sha256,
                       error_type = EXCLUDED.error_type,
                       error_message = EXCLUDED.error_message""",
                [
                    run_id, game_pk, SOURCE, endpoint, request_json,
                    retrieved_at, build_id, payload_sha256,
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def _create_tables(connection: Any) -> None:
    schedule_table = connection.execute(
        """SELECT count(*) FROM information_schema.tables
           WHERE table_schema = 'bronze' AND table_name = 'mlb_games'"""
    ).fetchone()[0]
    if not schedule_table:
        raise ValueError("bronze.mlb_games must be populated before detail backfill")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.mlb_game_detail_payloads (
            game_pk BIGINT PRIMARY KEY,
            source VARCHAR NOT NULL,
            endpoint VARCHAR NOT NULL,
            request_json JSON NOT NULL,
            retrieved_at TIMESTAMP NOT NULL,
            payload_sha256 VARCHAR NOT NULL,
            raw_path VARCHAR NOT NULL,
            ingestion_run_id VARCHAR NOT NULL,
            ingestion_build_id VARCHAR NOT NULL,
            payload_json JSON NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bronze.mlb_game_detail_attempts (
            ingestion_run_id VARCHAR NOT NULL,
            game_pk BIGINT NOT NULL,
            source VARCHAR NOT NULL,
            endpoint VARCHAR NOT NULL,
            request_json JSON NOT NULL,
            attempted_at TIMESTAMP NOT NULL,
            ingestion_build_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL CHECK (status IN ('fetched', 'missing', 'failed')),
            payload_sha256 VARCHAR,
            error_type VARCHAR,
            error_message VARCHAR,
            PRIMARY KEY (ingestion_run_id, game_pk),
            CHECK ((status = 'fetched' AND payload_sha256 IS NOT NULL
                    AND error_type IS NULL AND error_message IS NULL)
                OR (status <> 'fetched' AND payload_sha256 IS NULL))
        );
        CREATE INDEX IF NOT EXISTS mlb_game_detail_attempt_status
            ON bronze.mlb_game_detail_attempts (status, game_pk);
        CREATE OR REPLACE VIEW bronze.mlb_game_detail_status AS
        WITH latest_attempt AS (
            SELECT *, row_number() OVER (
                PARTITION BY game_pk
                ORDER BY attempted_at DESC, ingestion_run_id DESC
            ) AS attempt_rank
            FROM bronze.mlb_game_detail_attempts
        )
        SELECT
            games.game_pk,
            CASE
                WHEN payloads.game_pk IS NOT NULL THEN 'fetched'
                WHEN attempts.status IS NOT NULL THEN attempts.status
                ELSE 'pending'
            END AS status,
            payloads.payload_sha256,
            attempts.attempted_at AS last_attempted_at,
            attempts.ingestion_run_id AS last_ingestion_run_id,
            attempts.error_type,
            attempts.error_message
        FROM bronze.mlb_games AS games
        LEFT JOIN bronze.mlb_game_detail_payloads AS payloads USING (game_pk)
        LEFT JOIN latest_attempt AS attempts
            ON attempts.game_pk = games.game_pk AND attempts.attempt_rank = 1
        """
    )
