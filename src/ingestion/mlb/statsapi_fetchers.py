"""Adapters that turn the MLB-StatsAPI wrapper (toddrob99 ``statsapi``) into the
exact-bytes fetcher callables the ingestion layer expects.

ADR-004 designates the MLB StatsAPI wrapper as the historical baseball source.
The wrapper returns parsed JSON objects; per ADR-004 that wrapper response *is*
the retained "raw API response". We serialize it to canonical JSON bytes (sorted
keys, compact separators) so that a given wrapper response always yields
identical bytes and therefore an identical SHA-256. That keeps Bronze immutable
and makes restarts idempotent: DATA-005 hash-skips a ``game_pk`` whose retained
payload already matches, and DATA-010 records a retryable failure for transient
errors instead of aborting the whole backfill.

No network or wrapper import happens at module import time; ``statsapi`` is only
imported when a real fetcher is constructed without an injected client, which
keeps these adapters unit-testable with a fake ``get_json`` callable.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

# ``get_json(endpoint, params) -> parsed JSON`` — matches ``statsapi.get``.
GetJson = Callable[[str, Mapping[str, object]], Any]
Sleep = Callable[[float], None]

SCHEDULE_ENDPOINT = "schedule"
GAME_ENDPOINT = "game"

_GAME_PK_RE = re.compile(r"/game/(\d+)/feed/live")
_STATUS_CODE_RE = re.compile(r"status code[:\s]*([0-9]{3})", re.IGNORECASE)

DEFAULT_MIN_INTERVAL = 0.4
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 1.0


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize a parsed wrapper response to deterministic bytes.

    Sorted keys + compact separators guarantee the same logical response always
    produces the same bytes (and hash), independent of dict ordering.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def game_pk_from_endpoint(endpoint: str) -> int:
    """Extract the ``game_pk`` from a ``/api/v1.1/game/{pk}/feed/live`` path."""
    match = _GAME_PK_RE.search(endpoint)
    if match is None:
        raise ValueError(f"cannot extract game_pk from endpoint {endpoint!r}")
    return int(match.group(1))


def _status_code(error: Exception) -> int | None:
    """Best-effort HTTP status code from a wrapper error message (e.g. 404/503)."""
    match = _STATUS_CODE_RE.search(str(error))
    return int(match.group(1)) if match else None


class _RateLimiter:
    """Enforce a minimum interval between calls using an injectable clock/sleep."""

    def __init__(self, min_interval: float, sleep: Sleep, clock: Callable[[], float]):
        self._min_interval = max(0.0, min_interval)
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        if self._last is not None:
            elapsed = self._clock() - self._last
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def _default_get_json() -> GetJson:
    import statsapi  # imported lazily so tests need no network/wrapper

    return statsapi.get


def _call(
    get_json: GetJson,
    endpoint: str,
    params: Mapping[str, object],
    *,
    limiter: _RateLimiter,
    max_retries: int,
    backoff: float,
    sleep: Sleep,
) -> Any:
    """Call the wrapper with polite rate limiting and bounded retry.

    Permanent client errors (4xx other than 429) propagate immediately so they
    are never masked. Transient errors (timeouts, 5xx, 429, connection issues)
    are retried up to ``max_retries`` with exponential backoff, then re-raised.
    """
    attempt = 0
    while True:
        limiter.wait()
        try:
            return get_json(endpoint, dict(params))
        except Exception as error:  # noqa: BLE001 - re-raised below when not transient
            code = _status_code(error)
            permanent_client_error = code is not None and 400 <= code < 500 and code != 429
            attempt += 1
            if permanent_client_error or attempt > max_retries:
                raise
            sleep(backoff * (2 ** (attempt - 1)))


def make_schedule_fetcher(
    get_json: GetJson | None = None,
    *,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Sleep = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[Mapping[str, object]], bytes]:
    """Build a ``ScheduleFetcher`` backed by the MLB StatsAPI wrapper."""
    get_json = get_json or _default_get_json()
    limiter = _RateLimiter(min_interval, sleep, clock)

    def fetch_schedule(request: Mapping[str, object]) -> bytes:
        payload = _call(
            get_json, SCHEDULE_ENDPOINT, request,
            limiter=limiter, max_retries=max_retries, backoff=backoff, sleep=sleep,
        )
        return canonical_json_bytes(payload)

    return fetch_schedule


def make_game_detail_fetcher(
    get_json: GetJson | None = None,
    *,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Sleep = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[str, Mapping[str, object]], bytes | None]:
    """Build a ``GameDetailFetcher`` backed by the MLB StatsAPI wrapper.

    Returns ``None`` only for a genuine upstream not-found (HTTP 404) so the
    backfill records it as ``missing``; any other error propagates so DATA-010
    isolation records a retryable ``failed`` attempt without accepting a payload.
    """
    get_json = get_json or _default_get_json()
    limiter = _RateLimiter(min_interval, sleep, clock)

    def fetch_game_detail(endpoint: str, request: Mapping[str, object]) -> bytes | None:
        game_pk = game_pk_from_endpoint(endpoint)
        params = {"gamePk": game_pk, **dict(request)}
        try:
            payload = _call(
                get_json, GAME_ENDPOINT, params,
                limiter=limiter, max_retries=max_retries, backoff=backoff, sleep=sleep,
            )
        except Exception as error:  # noqa: BLE001
            if _status_code(error) == 404:
                return None
            raise
        return canonical_json_bytes(payload)

    return fetch_game_detail
