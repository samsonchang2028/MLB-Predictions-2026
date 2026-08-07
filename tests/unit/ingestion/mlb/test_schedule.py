import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ingestion.mlb import ingest_schedule


def _payload(*games: dict[str, object]) -> bytes:
    return json.dumps({"dates": [{"date": "2025-04-01", "games": games}]}).encode()


def _game(game_pk: int) -> dict[str, object]:
    return {
        "gamePk": game_pk,
        "season": "2025",
        "gameType": "R",
        "gameDate": "2025-04-01T20:10:00Z",
        "officialDate": "2025-04-01",
        "teams": {
            "away": {"team": {"id": 110}},
            "home": {"team": {"id": 137}},
        },
        "status": {
            "abstractGameState": "Preview",
            "codedGameState": "S",
            "detailedState": "Scheduled",
            "statusCode": "S",
        },
    }


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


def test_duplicate_game_pk_in_one_response_is_a_cardinality_error(tmp_path: Path) -> None:
    response = _payload(_game(1), _game(1))

    with pytest.raises(ValueError, match="duplicate gamePk 1"):
        ingest_schedule(
            tmp_path,
            lambda _: response,
            season=2025,
            fetched_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
        )


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
