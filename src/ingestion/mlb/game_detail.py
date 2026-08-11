"""Backfill immutable MLB game-detail payloads by ``game_pk``."""

from __future__ import annotations

import hashlib
import json
import re
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
BUILD_ID = "DATA-016"

# One filtered live-feed request provides probable starters and boxscore pitching
# lines without retaining play-by-play or pitch-level content.
#
# MLB ``fields=`` is an allowlist applied at EVERY nesting level. Two API
# semantics shape this list (DATA-016 P0 incident):
#   * The boxscore ``players`` object is keyed by dynamic ids (e.g. "ID660271").
#     Listing ``players`` itself empties the subtree, so it is deliberately
#     UNLISTED; the dynamic-id entries are still returned because their listed
#     descendants (``person`` id/fullName and the pitching leaf keys below) match.
#   * A leaf key is returned ONLY if it is listed. The prior projection listed the
#     identity leaves (``id``/``fullName``, so ``person`` came back populated) but
#     omitted the pitching-stat leaves, so the API returned ``stats: {}`` for every
#     player and silver.pitcher_appearances landed 100% NULL. The stat leaves must
#     therefore be listed explicitly.
#
# Only the pitching-line values FEAT-002 (starter) and FEAT-003 (bullpen) consume
# are added; no play-by-play, pitch-level, batting, or seasonStats data is
# requested. Each added leaf maps to a silver.pitcher_appearances column.
GAME_DETAIL_FIELDS = ",".join(
    (
        # navigation + identity: locate the game, probable starters, each team
        # boxscore, its ordered ``pitchers`` list, and each player ``person``.
        "gamePk", "gameData", "probablePitchers", "away", "home", "id",
        "fullName", "liveData", "boxscore", "teams", "team", "pitchers",
        # stat containers: without ``stats`` + ``pitching`` the leaves below are
        # unreachable and the whole line collapses to ``{}``.
        "stats", "pitching",
        # pitching-line leaves (each -> a silver.pitcher_appearances column):
        "inningsPitched",   # innings_pitched (IP text)
        "outs",             # outs_recorded (workload; IP = outs/3)
        "battersFaced",     # batters_faced (K%/BB% denominator)
        "numberOfPitches",  # pitches_thrown (prev-start / bullpen workload)
        "hits",             # hits_allowed (WHIP)
        "runs",             # runs_allowed (line completeness / sanity)
        "earnedRuns",       # earned_runs (ERA)
        "baseOnBalls",      # walks (WHIP, BB%)
        "strikeOuts",       # strikeouts (K%)
        "homeRuns",         # home_runs_allowed (line completeness)
    )
)

# A boxscore pitcher (one listed in a team's ``pitchers`` array) actually threw,
# so a completed game must carry a real pitching line for them. These are the
# leaves FEAT-002/FEAT-003 need; their absence means the ``fields`` projection
# silently dropped the stats (the DATA-016 defect), which must fail ingestion
# rather than persist a hollow row.
_REQUIRED_PITCHING_STATS = (
    "inningsPitched", "outs", "battersFaced", "numberOfPitches",
    "earnedRuns", "hits", "baseOnBalls", "strikeOuts",
)
_PITCHING_INT_STATS = (
    "outs", "battersFaced", "numberOfPitches", "hits", "runs",
    "earnedRuns", "baseOnBalls", "strikeOuts", "homeRuns",
)
_INNINGS_PITCHED_RE = re.compile(r"^\d+\.[012]$")


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
            lifecycle = _game_lifecycle(connection, game_pk)
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
            payload_text = _validate_payload(payload, game_pk, lifecycle=lifecycle)
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


def invalidate_game_detail_payloads(
    storage_root: str | Path, game_pks: Iterable[int]
) -> int:
    """Delete stale ``bronze.mlb_game_detail_payloads`` rows so ``game_pks``
    become eligible for a genuine re-fetch (DATA-018).

    Only the pointer/index row is removed. The immutable, content-addressed
    raw file on disk under ``raw/`` is never touched -- it stays forever,
    simply unreferenced by any current payload row -- and
    ``bronze.mlb_game_detail_attempts`` (the honest historical audit trail) is
    neither deleted nor rewritten. A ``game_pk`` with no existing payload row
    is left alone (nothing to delete for it).

    Deleting a row here does NOT by itself trigger a re-fetch: the most recent
    ``bronze.mlb_game_detail_attempts`` row for the game_pk still shows
    ``'fetched'``, so a subsequent ``backfill_game_details`` call still needs
    ``retry_unresolved=True`` to actually re-fetch these game_pks -- that flag
    already exists and is unchanged by this function.

    Returns the number of payload rows actually deleted.
    """
    targets = _distinct_positive_ints(game_pks, "game_pks")
    if not targets:
        raise ValueError("game_pks must not be empty")
    paths = initialize_storage(storage_root)
    placeholders = ", ".join("?" for _ in targets)
    with connect_database(paths["database"]) as connection:
        # Self-heal like backfill_game_details: a storage root where detail
        # backfill has never run yet has no payloads table, in which case
        # there is nothing to invalidate (safe no-op), not a raw DB exception.
        _create_tables(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            matched = connection.execute(
                f"""SELECT count(*) FROM bronze.mlb_game_detail_payloads
                    WHERE game_pk IN ({placeholders})""",
                targets,
            ).fetchone()[0]
            connection.execute(
                f"""DELETE FROM bronze.mlb_game_detail_payloads
                    WHERE game_pk IN ({placeholders})""",
                targets,
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return matched


def _distinct_positive_ints(values: Iterable[int], name: str) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must contain positive integers")
        if value in seen:
            raise ValueError(f"{name} must not contain duplicates")
        seen.add(value)
        result.append(value)
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


def _validate_payload(
    payload: object,
    expected_game_pk: int,
    *,
    lifecycle: Mapping[str, object] | None = None,
) -> str:
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
    _assert_pitching_stats_present(response, expected_game_pk, lifecycle)
    return text


def _assert_pitching_stats_present(
    response: dict[str, Any],
    game_pk: int,
    lifecycle: Mapping[str, object] | None,
) -> None:
    """Reject a hollow boxscore: every listed pitcher must carry a real line.

    A pitcher listed in a team's boxscore ``pitchers`` array actually took the
    mound, so their ``stats.pitching`` must contain the required nested values.
    An empty ``stats: {}`` (the DATA-016 projection defect) or an all-zero,
    no-activity line is a defect caught here, at the earliest point, instead of
    being persisted as NULL silver rows. Games with no boxscore pitchers
    (postponed/cancelled/suspended-before-play) are left untouched: they have
    empty ``pitchers`` arrays and legitimately carry no line.
    """
    completed = _is_completed_lifecycle(lifecycle)
    live_data = response.get("liveData")
    if not isinstance(live_data, dict):
        if completed:
            raise _hollow_structure(game_pk, "liveData object")
        return
    boxscore = live_data.get("boxscore")
    if not isinstance(boxscore, dict):
        if completed:
            raise _hollow_structure(game_pk, "liveData.boxscore object")
        return
    teams = boxscore.get("teams")
    if not isinstance(teams, dict):
        if completed:
            raise _hollow_structure(game_pk, "liveData.boxscore.teams object")
        return
    for side in ("home", "away"):
        side_box = teams.get(side)
        if not isinstance(side_box, dict):
            if completed:
                raise _hollow_structure(game_pk, f"{side} boxscore object")
            continue
        expected_team_id = (
            lifecycle.get(f"{side}_team_id") if lifecycle is not None else None
        )
        team = side_box.get("team")
        team_id = team.get("id") if isinstance(team, dict) else None
        if (
            completed
            and (
                isinstance(team_id, bool)
                or not isinstance(team_id, int)
                or team_id <= 0
                or (expected_team_id is not None and team_id != expected_team_id)
            )
        ):
            raise _hollow_structure(game_pk, f"valid {side} team identity")
        pitchers = side_box.get("pitchers")
        if not isinstance(pitchers, list):
            if completed:
                raise _hollow_structure(game_pk, f"{side} pitchers list")
            continue
        if not pitchers:
            if completed:
                raise _hollow_structure(game_pk, f"non-empty {side} pitchers list")
            continue
        players = side_box.get("players")
        if not isinstance(players, dict):
            raise _hollow_structure(game_pk, f"{side} players object")
        seen: set[int] = set()
        for pitcher_id in pitchers:
            if (
                isinstance(pitcher_id, bool)
                or not isinstance(pitcher_id, int)
                or pitcher_id <= 0
                or pitcher_id in seen
            ):
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher ids must be "
                    "unique positive integers"
                )
            seen.add(pitcher_id)
            player = players.get(f"ID{pitcher_id}")
            person = player.get("person") if isinstance(player, dict) else None
            if not isinstance(person, dict) or person.get("id") != pitcher_id:
                raise _hollow_structure(
                    game_pk, f"{side} player identity for pitcher {pitcher_id}"
                )
            stats = player.get("stats") if isinstance(player, dict) else None
            pitching = stats.get("pitching") if isinstance(stats, dict) else None
            if not isinstance(pitching, dict) or not pitching:
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher {pitcher_id} has "
                    f"empty pitching stats: hollow boxscore payload (projection defect)"
                )
            missing = [
                key for key in _REQUIRED_PITCHING_STATS if pitching.get(key) is None
            ]
            if missing:
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher {pitcher_id} is "
                    f"missing required pitching stats {missing}: hollow boxscore payload"
                )
            innings = pitching["inningsPitched"]
            if not isinstance(innings, str) or not _INNINGS_PITCHED_RE.fullmatch(
                innings
            ):
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher {pitcher_id} "
                    "inningsPitched must use N.[012] text"
                )
            malformed = [
                key
                for key in _PITCHING_INT_STATS
                if key in pitching
                and (
                    isinstance(pitching[key], bool)
                    or not isinstance(pitching[key], int)
                    or pitching[key] < 0
                )
            ]
            if malformed:
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher {pitcher_id} "
                    f"pitching stats must be non-negative integers {malformed}"
                )
            outs = pitching.get("outs")
            faced = pitching.get("battersFaced")
            recorded_activity = (
                (isinstance(outs, int) and not isinstance(outs, bool) and outs > 0)
                or (isinstance(faced, int) and not isinstance(faced, bool) and faced > 0)
            )
            if not recorded_activity:
                raise ValueError(
                    f"game-detail game_pk {game_pk} {side} pitcher {pitcher_id} has an "
                    f"all-zero pitching line (no outs or batters faced): hollow payload"
                )


def _game_lifecycle(connection: Any, game_pk: int) -> dict[str, object]:
    row = connection.execute(
        """SELECT abstract_game_state, detailed_state, coded_game_state,
                  status_code, home_team_id, away_team_id
           FROM bronze.mlb_games WHERE game_pk = ?""",
        [game_pk],
    ).fetchone()
    if row is None:
        raise ValueError(f"game_pk {game_pk} is absent from bronze.mlb_games")
    return dict(
        zip(
            (
                "abstract_game_state", "detailed_state", "coded_game_state",
                "status_code", "home_team_id", "away_team_id",
            ),
            row,
        )
    )


def _is_completed_lifecycle(lifecycle: Mapping[str, object] | None) -> bool:
    """Whether the schedule says the game genuinely completed.

    ``None`` is treated strictly as completed: production backfill always supplies
    schedule lifecycle, while direct callers must opt into a known non-played
    exception rather than accidentally accepting a hollow payload.
    """
    if lifecycle is None:
        return True
    coded = lifecycle.get("coded_game_state")
    if coded in ("F", "O"):
        return True
    if coded in ("C", "D", "U", "T", "S", "P", "I", "M"):
        return False
    detailed = lifecycle.get("detailed_state")
    if isinstance(detailed, str) and detailed.casefold().startswith(
        ("postponed", "suspended", "cancelled", "rescheduled")
    ):
        return False
    return lifecycle.get("abstract_game_state") == "Final"


def _hollow_structure(game_pk: int, expected: str) -> ValueError:
    return ValueError(
        f"game-detail game_pk {game_pk} completed game is missing {expected}: "
        "hollow boxscore payload"
    )


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
