"""Unit tests for the MLB-StatsAPI fetcher adapters (DATA-011). No network."""

from __future__ import annotations

import json

import pytest

from ingestion.mlb.statsapi_fetchers import (
    canonical_json_bytes,
    game_pk_from_endpoint,
    make_game_detail_fetcher,
    make_schedule_fetcher,
)

_NO_SLEEP = lambda _seconds: None  # noqa: E731 - test stub


class _Recorder:
    """Fake statsapi.get capturing calls and returning canned payloads/errors."""

    def __init__(self, result=None, *, error=None, errors=None):
        self.result = result
        self.error = error
        self.errors = list(errors) if errors else None
        self.calls = []

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        elif self.error is not None:
            raise self.error
        return self.result


def test_canonical_json_bytes_is_key_order_independent() -> None:
    a = canonical_json_bytes({"b": 1, "a": [3, 2]})
    b = canonical_json_bytes({"a": [3, 2], "b": 1})
    assert a == b
    # Round-trips to the same logical object.
    assert json.loads(a) == {"a": [3, 2], "b": 1}


def test_game_pk_from_endpoint() -> None:
    assert game_pk_from_endpoint("/api/v1.1/game/746817/feed/live") == 746817
    with pytest.raises(ValueError):
        game_pk_from_endpoint("/api/v1/schedule")


def test_schedule_fetcher_returns_canonical_bytes() -> None:
    payload = {"dates": [{"date": "2024-04-01", "games": []}]}
    recorder = _Recorder(result=payload)
    fetch = make_schedule_fetcher(recorder, min_interval=0.0, sleep=_NO_SLEEP)

    out = fetch({"sportId": 1, "season": 2024})

    assert out == canonical_json_bytes(payload)
    assert recorder.calls == [("schedule", {"sportId": 1, "season": 2024})]


def test_game_detail_fetcher_passes_game_pk_and_fields() -> None:
    payload = {"gamePk": 746817, "liveData": {}}
    recorder = _Recorder(result=payload)
    fetch = make_game_detail_fetcher(recorder, min_interval=0.0, sleep=_NO_SLEEP)

    out = fetch("/api/v1.1/game/746817/feed/live", {"fields": "gamePk,liveData"})

    assert out == canonical_json_bytes(payload)
    assert recorder.calls == [
        ("game", {"gamePk": 746817, "fields": "gamePk,liveData"})
    ]


def test_game_detail_fetcher_returns_none_on_404() -> None:
    recorder = _Recorder(error=ValueError("Request failed. Status Code: 404."))
    fetch = make_game_detail_fetcher(
        recorder, min_interval=0.0, max_retries=2, sleep=_NO_SLEEP
    )

    assert fetch("/api/v1.1/game/1/feed/live", {"fields": "gamePk"}) is None
    # 404 is permanent: no retry.
    assert len(recorder.calls) == 1


def test_game_detail_fetcher_reraises_non_404_after_retries() -> None:
    recorder = _Recorder(error=ValueError("Request failed. Status Code: 503."))
    fetch = make_game_detail_fetcher(
        recorder, min_interval=0.0, max_retries=2, backoff=0.0, sleep=_NO_SLEEP
    )

    with pytest.raises(ValueError, match="503"):
        fetch("/api/v1.1/game/1/feed/live", {"fields": "gamePk"})
    # initial attempt + 2 retries
    assert len(recorder.calls) == 3


def test_transient_error_then_success_is_retried() -> None:
    payload = {"gamePk": 5, "liveData": {}}
    recorder = _Recorder(errors=[TimeoutError("temporary"), None])
    recorder.result = payload
    fetch = make_game_detail_fetcher(
        recorder, min_interval=0.0, max_retries=3, backoff=0.0, sleep=_NO_SLEEP
    )

    out = fetch("/api/v1.1/game/5/feed/live", {"fields": "gamePk"})
    assert out == canonical_json_bytes(payload)
    assert len(recorder.calls) == 2


def test_rate_limiter_sleeps_between_calls() -> None:
    slept: list[float] = []
    # First wait() consumes one reading (sets _last=0.0). Second wait() reads
    # 0.1 for elapsed (=> sleep 0.4), then 0.1 to reset _last.
    clock = iter([0.0, 0.1, 0.1])

    def fake_clock() -> float:
        return next(clock)

    recorder = _Recorder(result={"ok": True})
    fetch = make_schedule_fetcher(
        recorder, min_interval=0.5, sleep=slept.append, clock=fake_clock
    )
    fetch({"a": 1})
    fetch({"a": 2})
    # Second call must wait ~0.4s (0.5 interval minus 0.1 elapsed).
    assert slept and abs(slept[0] - 0.4) < 1e-9
