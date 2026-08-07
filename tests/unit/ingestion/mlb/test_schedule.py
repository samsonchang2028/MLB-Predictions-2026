import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ingestion.mlb import ingest_schedule
from storage import connect_database, storage_paths


def _payload(*games: dict[str, object]) -> bytes:
    return json.dumps({"dates": [{"date": "2025-04-01", "games": games}]}).encode()


def _game(game_pk: int, **overrides: object) -> dict[str, object]:
    status = {
        "abstractGameState": overrides.pop("abstractGameState", "Preview"),
        "codedGameState": overrides.pop("codedGameState", "S"),
        "detailedState": overrides.pop("detailedState", "Scheduled"),
        "statusCode": overrides.pop("statusCode", "S"),
    }
    game: dict[str, object] = {
        "gamePk": game_pk,
        "season": "2025",
        "gameType": "R",
        "gameDate": "2025-04-01T20:10:00Z",
        "officialDate": "2025-04-01",
        "teams": {
            "away": {"team": {"id": 110}},
            "home": {"team": {"id": 137}},
        },
        "status": status,
    }
    game.update(overrides)
    return game


def test_season_ingestion_builds_expected_request_and_retains_exact_bytes(
    tmp_path: Path,
) -> None:
    response = b'{"dates": []}\n'
    requests: list[object] = []

    result = ingest_schedule(
        tmp_path,
        lambda parameters: requests.append(parameters) or response,
        season=2025,
        fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    assert requests == [{"sportId": 1, "season": 2025}]
    assert result["games_seen"] == 0
    assert result["raw_path"].read_bytes() == response


def test_date_range_ingestion_accepts_dates_and_iso_strings(tmp_path: Path) -> None:
    requests: list[object] = []

    ingest_schedule(
        tmp_path,
        lambda parameters: requests.append(parameters) or b'{"dates": []}',
        start_date=date(2025, 4, 1),
        end_date="2025-04-03",
        fetched_at=datetime(2025, 4, 4, tzinfo=timezone.utc),
    )

    assert requests == [
        {"sportId": 1, "startDate": "2025-04-01", "endDate": "2025-04-03"}
    ]


@pytest.mark.parametrize("season", [1876, datetime.now(timezone.utc).year])
def test_valid_season_boundaries_are_accepted(tmp_path: Path, season: int) -> None:
    requests: list[object] = []

    ingest_schedule(
        tmp_path,
        lambda parameters: requests.append(parameters) or b'{"dates": []}',
        season=season,
        fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    assert requests == [{"sportId": 1, "season": season}]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"season": 2025, "start_date": "2025-04-01", "end_date": "2025-04-02"},
        {"start_date": "2025-04-02", "end_date": "2025-04-01"},
        {"start_date": "20250401", "end_date": "2025-04-02"},
        {"season": 9999},
    ],
)
def test_invalid_query_scope_is_rejected_before_fetch(
    tmp_path: Path, kwargs: dict[str, object]
) -> None:
    called = False

    def fetch(_: object) -> bytes:
        nonlocal called
        called = True
        return b'{"dates": []}'

    with pytest.raises(ValueError):
        ingest_schedule(tmp_path, fetch, **kwargs)

    assert not called


def test_repeated_game_pk_in_one_response_is_reconciled_not_rejected(
    tmp_path: Path,
) -> None:
    # DATA-013: this test previously asserted that a repeated gamePk within one
    # response is a hard cardinality error. That assumption is incorrect against
    # real MLB data: a season-wide schedule legitimately lists a postponed game
    # on its original date and its makeup on the reschedule date under one
    # game_pk (81 game_pks were duplicated in 2021 alone). Benign identical
    # repeats must now reconcile to exactly one canonical row rather than abort
    # the build, so the assertion is inverted here rather than deleted.
    response = _payload(_game(1), _game(1))

    result = ingest_schedule(
        tmp_path,
        lambda _: response,
        season=2025,
        fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
    )

    assert result["games_seen"] == 1
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        canonical = connection.execute(
            "SELECT count(*) FROM bronze.mlb_games WHERE game_pk = 1"
        ).fetchone()[0]
        observations = connection.execute(
            "SELECT count(*) FROM bronze.mlb_game_observations WHERE game_pk = 1"
        ).fetchone()[0]
    assert canonical == 1
    assert observations == 1


def test_repeated_game_pk_prefers_final_and_preserves_reschedule_metadata(
    tmp_path: Path,
) -> None:
    postponed = _game(
        7,
        detailedState="Postponed",
        codedGameState="D",
        statusCode="DR",
        rescheduleDate="2025-04-03",
    )
    # abstractGameState 'Final' on the postponed placeholder mirrors real MLB
    # data (DATA-012); precedence must still resolve on codedGameState.
    postponed["status"]["abstractGameState"] = "Final"
    makeup = _game(
        7,
        detailedState="Final",
        codedGameState="F",
        statusCode="F",
        gameDate="2025-04-03T20:10:00Z",
        officialDate="2025-04-03",
        rescheduledFromDate="2025-04-01",
    )
    makeup["status"]["abstractGameState"] = "Final"
    response = json.dumps(
        {
            "dates": [
                {"date": "2025-04-01", "games": [postponed]},
                {"date": "2025-04-03", "games": [makeup]},
            ]
        }
    ).encode()

    ingest_schedule(
        tmp_path,
        lambda _: response,
        season=2025,
        fetched_at=datetime(2025, 4, 4, tzinfo=timezone.utc),
    )

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        row = connection.execute(
            """
            SELECT detailed_state, coded_game_state, official_date,
                   reschedule_date, rescheduled_from_date
            FROM bronze.mlb_games WHERE game_pk = 7
            """
        ).fetchone()
        canonical = connection.execute(
            "SELECT count(*) FROM bronze.mlb_games WHERE game_pk = 7"
        ).fetchone()[0]
        observations = connection.execute(
            "SELECT count(*) FROM bronze.mlb_game_observations WHERE game_pk = 7"
        ).fetchone()[0]

    assert canonical == 1
    assert observations == 1
    # Final outcome state wins; the postponed placeholder's rescheduleDate and
    # the makeup's rescheduledFromDate are both preserved on the canonical row.
    assert row == ("Final", "F", date(2025, 4, 3), "2025-04-03", "2025-04-01")


def test_repeated_game_pk_with_conflicting_teams_fails(tmp_path: Path) -> None:
    original = _game(8)
    swapped = _game(8)
    swapped["teams"]["home"]["team"]["id"] = 999
    response = _payload(original, swapped)

    with pytest.raises(ValueError, match="conflicting home/away teams"):
        ingest_schedule(
            tmp_path,
            lambda _: response,
            season=2025,
            fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        )


def test_conflicting_entries_at_same_lifecycle_stage_fail(tmp_path: Path) -> None:
    # DATA-014 refined the same-top-stage guard to compare only outcome-
    # identifying fields (home/away team id, home/away score, winner). A genuine
    # outcome disagreement between two Final entries (here, a differing home
    # score and winner) still cannot be silently resolved and must fail.
    first = _game(9, detailedState="Final", codedGameState="F", statusCode="F")
    first["teams"]["home"].update({"score": 6, "isWinner": True})
    first["teams"]["away"].update({"score": 5, "isWinner": False})
    second = _game(9, detailedState="Final", codedGameState="F", statusCode="F")
    second["teams"]["home"].update({"score": 3, "isWinner": False})
    second["teams"]["away"].update({"score": 4, "isWinner": True})
    response = _payload(first, second)

    with pytest.raises(ValueError, match="conflicting duplicate entries"):
        ingest_schedule(
            tmp_path,
            lambda _: response,
            season=2025,
            fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        )


def test_same_stage_identical_outcome_suspended_resume_reconciles(
    tmp_path: Path,
) -> None:
    # DATA-014: a suspended-then-resumed game lists the same game_pk under both
    # its original date (Final + resumeDate) and its resumption date (Final +
    # resumedFromDate). The outcome is identical; only scheduling/linkage
    # metadata differs, so the two Final entries reconcile to one canonical row
    # rather than failing the same-top-stage guard.
    original = _game(11, detailedState="Final", codedGameState="F", statusCode="F")
    original["teams"]["home"].update({"score": 6, "isWinner": True})
    original["teams"]["away"].update({"score": 5, "isWinner": False})
    original["resumeDate"] = "2025-08-31T20:10:00Z"
    resumption = _game(11, detailedState="Final", codedGameState="F", statusCode="F")
    resumption["teams"]["home"].update({"score": 6, "isWinner": True})
    resumption["teams"]["away"].update({"score": 5, "isWinner": False})
    resumption["gameDate"] = "2025-08-31T20:10:00Z"
    resumption["resumedFromDate"] = "2025-04-01"
    response = json.dumps(
        {
            "dates": [
                {"date": "2025-04-01", "games": [original]},
                {"date": "2025-08-31", "games": [resumption]},
            ]
        }
    ).encode()

    result = ingest_schedule(
        tmp_path,
        lambda _: response,
        season=2025,
        fetched_at=datetime(2025, 9, 1, tzinfo=timezone.utc),
    )

    assert result["games_seen"] == 1
    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        canonical = connection.execute(
            "SELECT count(*) FROM bronze.mlb_games WHERE game_pk = 11"
        ).fetchone()[0]
        observations = connection.execute(
            "SELECT count(*) FROM bronze.mlb_game_observations WHERE game_pk = 11"
        ).fetchone()[0]
        row = connection.execute(
            """
            SELECT resume_date, game_date, official_date, game_json
            FROM bronze.mlb_games WHERE game_pk = 11
            """
        ).fetchone()

    assert canonical == 1
    assert observations == 1
    # Canonical selection picks the completion (resumption) entry: latest
    # gameDate and the carried resume linkage on both sides.
    assert row[0] == "2025-08-31T20:10:00Z"  # resumeDate merged from original
    assert row[1] == datetime(2025, 8, 31, 20, 10)  # resumption gameDate
    assert row[2] == date(2025, 4, 1)  # officialDate preserved
    assert json.loads(row[3])["resumedFromDate"] == "2025-04-01"


def test_naive_ingestion_timestamp_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        ingest_schedule(
            tmp_path,
            lambda _: b'{"dates": []}',
            season=2025,
            fetched_at=datetime(2025, 1, 1),
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"message": "upstream unavailable"}, "dates list"),
        ({"dates": None}, "dates list"),
        ({"dates": [{}]}, "games list"),
        ({"dates": [{"games": None}]}, "games list"),
    ],
)
def test_upstream_error_or_partial_envelope_is_rejected(
    tmp_path: Path, response: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ingest_schedule(
            tmp_path,
            lambda _: json.dumps(response).encode(),
            season=2025,
            fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda game: game.pop("gamePk"), "integer gamePk"),
        (lambda game: game.__setitem__("gamePk", -1), "integer gamePk"),
        (lambda game: game.pop("gameDate"), "gameDate timestamp"),
        (lambda game: game.__setitem__("gameDate", "2025-04-01T20:10:00"), "timezone"),
        (lambda game: game.pop("officialDate"), "officialDate"),
        (lambda game: game.__setitem__("officialDate", "20250401"), "officialDate"),
        (lambda game: game.pop("status"), "status object"),
        (
            lambda game: game["status"].pop("statusCode"),
            "status must have statusCode",
        ),
    ],
)
def test_partial_canonical_game_is_rejected(
    tmp_path: Path, mutation: object, message: str
) -> None:
    game = _game(1)
    mutation(game)

    with pytest.raises(ValueError, match=message):
        ingest_schedule(
            tmp_path,
            lambda _: _payload(game),
            season=2025,
            fetched_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
