"""Map Kalshi per-game MLB markets to this repo's canonical ``game_pk``.

Kalshi identifies a game through an ``event_ticker``/title with free-text,
city-style team names (e.g. ``yes_sub_title="Seattle"``,
``no_sub_title="Houston"`` -- confirmed against
``tests/unit/ingestion/kalshi/fixtures/kalshi_market_snapshots.json``, a real
captured payload), not a structured home/away pair of full club names like
The Odds API. Kalshi's own docs (docs.kalshi.com) also warn against parsing
ticker strings to infer relationships, so this module reads
``yes_sub_title``/``no_sub_title``/``occurrence_datetime`` off the market
object, not the ticker text.

This mirrors ``scripts/daily_predictions.py``'s ``_match_schedule_game``
tolerance/ambiguity *pattern* (near-start-time tolerance for doubleheader
disambiguation, explicit match/no-match/ambiguous outcome) without reusing
that function directly -- the team-name extraction differs because Kalshi's
input shape (free-text city name) differs from The Odds API's (structured
home/away team names already matching this repo's schedule data verbatim).

Every Kalshi market resolves to exactly one of MATCHED / UNMATCHED /
AMBIGUOUS, never a bare ``None``, mirroring DATA-009's audit-visible outcome
categories in ``src/validation/odds_mapping.py``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
AMBIGUOUS = "AMBIGUOUS"

# Same doubleheader-disambiguation tolerance as
# scripts/daily_predictions.py's ODDS_MATCH_TOLERANCE_SECONDS (12 hours) --
# kept as a local constant with the same value rather than importing that
# script, which would make an src/ module depend on an operator script that
# pulls in duckdb/xgboost/network imports at load time.
MATCH_TIME_TOLERANCE_SECONDS = 12 * 60 * 60


class KalshiMatchingError(ValueError):
    """Raised when a Kalshi market is missing a field needed to attempt a match."""


@dataclass(frozen=True)
class KalshiGameCandidate:
    """One ``silver.games`` schedule entry usable as a Kalshi-match target."""

    game_pk: int
    start_time: datetime
    home_team_norm: str
    away_team_norm: str


@dataclass(frozen=True)
class KalshiMatchResult:
    """Auditable match outcome for one Kalshi market (never a bare ``None``)."""

    market_ticker: str
    event_ticker: str
    status: str
    reason: str
    game_pk: int | None
    candidate_game_pks: tuple[int, ...]


def normalize_kalshi_team_name(value: str) -> str:
    """Casefold + strip periods + collapse whitespace.

    Same normalization style as ``_normalize_team_name`` in
    ``scripts/daily_predictions.py`` (stylistic consistency required by the
    task), reimplemented locally for the same reason as
    :data:`MATCH_TIME_TOLERANCE_SECONDS`.
    """
    return " ".join(str(value).casefold().replace(".", "").split())


def match_kalshi_market(
    market: Mapping[str, Any], schedule: Sequence[KalshiGameCandidate]
) -> KalshiMatchResult:
    """Match one Kalshi market (DATA-022 payload shape) to a ``game_pk``.

    ``market`` is one entry of a Kalshi ``GET /markets`` response (see the
    real fixture): ``ticker``, ``event_ticker``, ``yes_sub_title``,
    ``no_sub_title``, ``occurrence_datetime`` are required. Kalshi does not
    label which side is home/away, so the two team names are matched against
    the schedule's home/away pair in either order.
    """
    market_ticker = _required(market, "ticker")
    event_ticker = _required(market, "event_ticker")
    team_a = normalize_kalshi_team_name(_required(market, "yes_sub_title"))
    team_b = normalize_kalshi_team_name(_required(market, "no_sub_title"))
    game_datetime = _parse_occurrence_datetime(_required(market, "occurrence_datetime"))

    candidates = [
        game
        for game in schedule
        if _team_pair_matches(team_a, team_b, game.home_team_norm, game.away_team_norm)
    ]
    if not candidates:
        return _result(market_ticker, event_ticker, UNMATCHED, "no_team_match", None, ())

    candidate_pks = tuple(sorted(game.game_pk for game in candidates))
    ranked = sorted(
        (
            (abs((game.start_time - game_datetime).total_seconds()), game)
            for game in candidates
        ),
        key=lambda item: (item[0], item[1].game_pk),
    )
    nearest_seconds, nearest_game = ranked[0]
    if nearest_seconds > MATCH_TIME_TOLERANCE_SECONDS:
        return _result(
            market_ticker, event_ticker, UNMATCHED, "time_out_of_tolerance", None, candidate_pks
        )
    if len(ranked) > 1 and ranked[1][0] == nearest_seconds:
        return _result(
            market_ticker, event_ticker, AMBIGUOUS, "ambiguous_nearest_time", None, candidate_pks
        )
    return _result(
        market_ticker, event_ticker, MATCHED, "matched", nearest_game.game_pk, (nearest_game.game_pk,)
    )


def summarize_match_results(results: Sequence[KalshiMatchResult]) -> dict[str, int]:
    """Loggable match-outcome counts, e.g. ``{"matched_events": 3, "unmatched_events.no_team_match": 1}``.

    Same ``unmatched_events.<reason>`` shape as ``odds_stats`` in
    ``scripts/daily_predictions.py``'s ``odds_snapshots_for_schedule``.
    """
    stats: Counter[str] = Counter()
    for result in results:
        if result.status == MATCHED:
            stats["matched_events"] += 1
        else:
            stats[f"unmatched_events.{result.reason}"] += 1
    stats["mapped_games"] = sum(1 for result in results if result.status == MATCHED)
    return dict(stats)


def kalshi_game_candidates_from_schedule(
    games: Sequence[Mapping[str, Any]],
) -> list[KalshiGameCandidate]:
    """Build match candidates from ``silver.games`` rows.

    Each row needs ``game_pk``, ``game_date`` (first pitch), and
    ``source_game_json`` -- same source of team names as
    ``_mlb_team_names``/``_candidate_team_names`` use elsewhere in this repo.
    """
    candidates = []
    for game in games:
        home, away = _schedule_team_names(game)
        candidates.append(
            KalshiGameCandidate(
                game_pk=int(game["game_pk"]),
                start_time=_utc_instant(game["game_date"]),
                home_team_norm=normalize_kalshi_team_name(home),
                away_team_norm=normalize_kalshi_team_name(away),
            )
        )
    return candidates


def _team_pair_matches(team_a: str, team_b: str, home_norm: str, away_norm: str) -> bool:
    return (_city_name_matches(team_a, home_norm) and _city_name_matches(team_b, away_norm)) or (
        _city_name_matches(team_a, away_norm) and _city_name_matches(team_b, home_norm)
    )


def _city_name_matches(kalshi_name: str, schedule_name: str) -> bool:
    """True if Kalshi's city-style name identifies the schedule's full club name.

    Kalshi titles use city-style short names (``"Seattle"``) rather than this
    repo's full official club name (``"Seattle Mariners"``), so this is
    word-subset containment on already-normalized text, not equality.

    Known limitation (judgment call, see task handoff): a bare one-word city
    shared by two clubs (e.g. ``"Chicago"`` for Cubs/White Sox, ``"New York"``
    for Yankees/Mets, ``"Los Angeles"`` for Dodgers/Angels) would satisfy this
    containment test against either club's full name. The only real fixture
    confirmed so far (Seattle/Houston) has no such collision, so this is
    unverified against a real multi-club-city Kalshi payload.
    """
    kalshi_words = set(kalshi_name.split())
    schedule_words = set(schedule_name.split())
    return bool(kalshi_words) and kalshi_words <= schedule_words


def _required(market: Mapping[str, Any], field: str) -> str:
    value = market.get(field)
    if not isinstance(value, str) or not value.strip():
        raise KalshiMatchingError(f"{field} is required to match a Kalshi market")
    return value.strip()


def _parse_occurrence_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise KalshiMatchingError(
            "occurrence_datetime must be a valid ISO-8601 timestamp"
        ) from error
    return _utc_instant(parsed)


def _utc_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _schedule_team_names(game: Mapping[str, Any]) -> tuple[str, str]:
    raw = game.get("source_game_json")
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, Mapping):
        raise KalshiMatchingError(
            f"game_pk={game.get('game_pk')} missing source_game_json mapping"
        )
    try:
        home = payload["teams"]["home"]["team"]["name"]
        away = payload["teams"]["away"]["team"]["name"]
    except (KeyError, TypeError) as exc:
        raise KalshiMatchingError(
            f"game_pk={game.get('game_pk')} missing team names in source_game_json"
        ) from exc
    return str(home), str(away)


def _result(
    market_ticker: str,
    event_ticker: str,
    status: str,
    reason: str,
    game_pk: int | None,
    candidate_game_pks: tuple[int, ...],
) -> KalshiMatchResult:
    return KalshiMatchResult(
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        status=status,
        reason=reason,
        game_pk=game_pk,
        candidate_game_pks=candidate_game_pks,
    )
