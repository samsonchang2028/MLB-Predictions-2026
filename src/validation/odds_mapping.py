"""Historical odds-archive validation and auditable odds->game_pk mapping (DATA-009).

This module validates the DATA-008 historical odds archive and maps each archive
game event to a canonical MLB ``game_pk`` **without ever guessing**. It reuses the
shared ``validation.results`` contract (``CheckResult`` / ``summarize``) read-only.

Mapping contract
----------------
The mapping unit is one archive *event* = ``(archive_date, source_game_index)``
(all sportsbook lines of one archived game share teams and start time). Every
event resolves to exactly one status:

- ``MATCHED``   — exactly one MLB game resolves; carries exactly one ``game_pk``.
- ``UNMATCHED`` — no date+team candidate; carries **no** ``game_pk``.
- ``AMBIGUOUS`` — multiple candidates that start time cannot disambiguate;
  carries **no** ``game_pk``.

Rules (AGENTS.md data rules + ADR-002 / ADR-004):

- Team/date alone never attaches when more than one MLB game matches. Same-day
  doubleheaders are disambiguated only by an exact game-start instant; if start
  time cannot single out one game the event stays ``AMBIGUOUS``.
- There is no "take the first candidate" fallback. Ambiguity is preserved, not
  resolved arbitrarily, so ambiguous rows stay out of canonical market
  evaluation.
- Every mapping record preserves its candidate count, candidate ``game_pk`` pool,
  and a machine-readable reason for audit.

MARKET-001 note
---------------
Opening moneylines on ``MATCHED`` events are the canonical historical market
benchmark and support only *model edge versus opening market* claims. They are
**not** the exact price available at an arbitrary historical prediction timestamp
(ADR-002 / ADR-004). ``current_*`` prices are post-hoc archive values, never
pregame timestamp-valid snapshots. ``UNMATCHED``/``AMBIGUOUS`` events must never
enter canonical opening-market evaluation.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ingestion.odds import PUBLISHED_SHA256
from validation.results import CheckResult, fail, finding, ok, warn

# --------------------------------------------------------------------------- #
# Status vocabulary
# --------------------------------------------------------------------------- #
MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
AMBIGUOUS = "AMBIGUOUS"

# American-odds sentinel bounds (mirrors DATA-008 ingestion validation).
_MIN_INTEGER = -(2**31)
_MAX_INTEGER = 2**31 - 1

# Default location for the coverage artifact (repository-relative, ADR-004
# "closest existing convention": simple versioned report, not console output).
DEFAULT_COVERAGE_REPORT = Path("reports/data-quality/odds_archive_coverage.json")


# --------------------------------------------------------------------------- #
# Deterministic team-name normalization
# --------------------------------------------------------------------------- #
def normalize_team_name(name: str) -> str:
    """Return a deterministic, idempotent canonical key for a team name.

    Casefolds, strips diacritics, drops periods, and collapses internal
    whitespace so ``"St. Louis  Cardinals"`` and ``"St Louis Cardinals"`` share
    one key. Pure and idempotent: ``normalize_team_name(normalize_team_name(x))
    == normalize_team_name(x)``.
    """
    if not isinstance(name, str):
        raise TypeError("team name must be a string")
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_periods = without_marks.replace(".", "")
    return " ".join(without_periods.casefold().split())


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GameCandidate:
    """One MLB Silver game usable as an odds-mapping target."""

    game_pk: int
    official_date: date
    start_time: datetime | None
    home_team_norm: str | None
    away_team_norm: str | None


@dataclass(frozen=True)
class ArchiveEvent:
    """One archived game (all its sportsbook lines share these fields)."""

    archive_date: date
    source_game_index: int
    game_start_time: datetime | None
    home_team: str
    away_team: str
    home_team_norm: str
    away_team_norm: str
    sportsbooks: tuple[str, ...]
    lines: tuple[Mapping[str, Any], ...]

    @property
    def event_id(self) -> tuple[date, int]:
        return (self.archive_date, self.source_game_index)


@dataclass(frozen=True)
class OddsGameMapping:
    """Auditable mapping outcome for one archive event."""

    archive_date: date
    source_game_index: int
    game_start_time: datetime | None
    home_team: str
    away_team: str
    home_team_norm: str
    away_team_norm: str
    status: str
    reason: str
    game_pk: int | None
    candidate_count: int
    candidate_game_pks: tuple[int, ...]
    resolved_by: str | None
    sportsbooks: tuple[str, ...]

    @property
    def event_id(self) -> tuple[date, int]:
        return (self.archive_date, self.source_game_index)


# --------------------------------------------------------------------------- #
# Event aggregation
# --------------------------------------------------------------------------- #
def build_archive_events(records: Iterable[Mapping[str, Any]]) -> list[ArchiveEvent]:
    """Group parsed archive line records into per-game events, deterministically.

    All lines of one ``(archive_date, source_game_index)`` must agree on teams
    and start time (they come from a single ``gameView``); disagreement is a
    hard data error, not something to silently paper over.
    """
    grouped: dict[tuple[date, int], list[Mapping[str, Any]]] = {}
    for record in records:
        key = (record["archive_date"], record["source_game_index"])
        grouped.setdefault(key, []).append(record)

    events: list[ArchiveEvent] = []
    for (archive_date, game_index), lines in sorted(grouped.items()):
        lines = sorted(lines, key=lambda r: r["source_line_index"])
        home_team = _single(lines, "home_team", archive_date, game_index)
        away_team = _single(lines, "away_team", archive_date, game_index)
        start_time = _single(lines, "game_start_time", archive_date, game_index)
        sportsbooks = tuple(sorted(str(line["sportsbook"]) for line in lines))
        events.append(
            ArchiveEvent(
                archive_date=archive_date,
                source_game_index=game_index,
                game_start_time=_as_utc(start_time),
                home_team=home_team,
                away_team=away_team,
                home_team_norm=normalize_team_name(home_team),
                away_team_norm=normalize_team_name(away_team),
                sportsbooks=sportsbooks,
                lines=tuple(lines),
            )
        )
    return events


def _single(
    lines: Sequence[Mapping[str, Any]], field: str, archive_date: date, game_index: int
) -> Any:
    values = {line[field] for line in lines}
    if len(values) != 1:
        raise ValueError(
            f"archive event {(archive_date.isoformat(), game_index)!r} has "
            f"conflicting {field} across sportsbook lines: {sorted(map(str, values))!r}"
        )
    return next(iter(values))


# --------------------------------------------------------------------------- #
# Mapping decisions
# --------------------------------------------------------------------------- #
def decide_mapping(
    event: ArchiveEvent, candidates: Sequence[GameCandidate]
) -> OddsGameMapping:
    """Decide one event's mapping status without ever guessing a game_pk."""
    pool = [
        candidate
        for candidate in candidates
        if candidate.official_date == event.archive_date
        and candidate.home_team_norm is not None
        and candidate.away_team_norm is not None
        and candidate.home_team_norm == event.home_team_norm
        and candidate.away_team_norm == event.away_team_norm
    ]
    pool_pks = tuple(sorted(candidate.game_pk for candidate in pool))

    if not pool:
        return _mapping(event, UNMATCHED, "no_date_team_candidate", None, 0, (), None)

    if len(pool) == 1:
        return _mapping(
            event,
            MATCHED,
            "unique_date_team_candidate",
            pool[0].game_pk,
            1,
            pool_pks,
            "date_team_unique",
        )

    # More than one MLB game shares this date + matchup (doubleheader / reschedule).
    # Team + date alone must NOT attach; require an exact start-instant match.
    if event.game_start_time is None:
        return _mapping(
            event,
            AMBIGUOUS,
            "doubleheader_no_event_start_time",
            None,
            len(pool),
            pool_pks,
            None,
        )
    start_matches = [
        candidate
        for candidate in pool
        if candidate.start_time is not None
        and candidate.start_time == event.game_start_time
    ]
    if len(start_matches) == 1:
        return _mapping(
            event,
            MATCHED,
            "unique_start_time_match_among_candidates",
            start_matches[0].game_pk,
            len(pool),
            pool_pks,
            "start_time_unique",
        )
    if not start_matches:
        return _mapping(
            event,
            AMBIGUOUS,
            "doubleheader_no_start_time_match",
            None,
            len(pool),
            pool_pks,
            None,
        )
    return _mapping(
        event,
        AMBIGUOUS,
        "doubleheader_multiple_start_time_matches",
        None,
        len(pool),
        pool_pks,
        None,
    )


def _mapping(
    event: ArchiveEvent,
    status: str,
    reason: str,
    game_pk: int | None,
    candidate_count: int,
    candidate_game_pks: tuple[int, ...],
    resolved_by: str | None,
) -> OddsGameMapping:
    return OddsGameMapping(
        archive_date=event.archive_date,
        source_game_index=event.source_game_index,
        game_start_time=event.game_start_time,
        home_team=event.home_team,
        away_team=event.away_team,
        home_team_norm=event.home_team_norm,
        away_team_norm=event.away_team_norm,
        status=status,
        reason=reason,
        game_pk=game_pk,
        candidate_count=candidate_count,
        candidate_game_pks=candidate_game_pks,
        resolved_by=resolved_by,
        sportsbooks=event.sportsbooks,
    )


def map_archive_events(
    events: Iterable[ArchiveEvent], candidates: Sequence[GameCandidate]
) -> list[OddsGameMapping]:
    """Map every event, returned in deterministic ``event_id`` order."""
    mappings = [decide_mapping(event, candidates) for event in events]
    mappings.sort(key=lambda m: m.event_id)
    return mappings


# --------------------------------------------------------------------------- #
# Validation checks (reuse validation.results CheckResult contract)
# --------------------------------------------------------------------------- #
def check_published_sha256_recorded(artifact: Mapping[str, Any] | None) -> CheckResult:
    check = "odds_archive.published_sha256_recorded"
    if not artifact:
        return fail(check, "P0", "no historical odds archive artifact recorded")
    recorded = artifact.get("payload_sha256")
    if recorded != PUBLISHED_SHA256:
        return fail(
            check,
            "P0",
            f"recorded archive sha256 {recorded!r} != published {PUBLISHED_SHA256!r}",
            [recorded],
        )
    return ok(check, "P0", "published SHA-256 recorded")


def check_source_file_immutability(
    artifact: Mapping[str, Any] | None, storage_root: str | Path
) -> CheckResult:
    """Retained raw file is byte-identical to its recorded hash (not mutated).

    Chained with :func:`check_published_sha256_recorded` (recorded == published),
    this proves ``file == recorded == published`` without re-embedding the
    published constant here.
    """
    check = "odds_archive.source_file_immutable"
    if not artifact:
        return fail(check, "P0", "no artifact to verify source-file immutability")
    raw_path = artifact.get("raw_path")
    recorded = artifact.get("payload_sha256")
    if not raw_path or not recorded:
        return fail(check, "P0", "artifact is missing raw_path or payload_sha256")
    path = Path(storage_root) / "raw" / raw_path
    if not path.exists():
        return fail(check, "P0", f"retained archive file is missing: {raw_path}", [raw_path])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != recorded:
        return fail(
            check,
            "P0",
            f"retained archive file hash {digest!r} != recorded {recorded!r}",
            [raw_path],
        )
    return ok(check, "P0", "retained source file matches its recorded SHA-256")


def check_moneylines_parse(records: Iterable[Mapping[str, Any]]) -> CheckResult:
    check = "odds_archive.moneylines_parse"
    bad: list[str] = []
    for record in records:
        for field in (
            "opening_home_american",
            "opening_away_american",
            "current_home_american",
            "current_away_american",
        ):
            if not _valid_american(record.get(field)):
                bad.append(f"{_record_ref(record)}:{field}={record.get(field)!r}")
    return finding(check, "P1", bad, "invalid American moneyline prices")


def check_sportsbook_identity_preserved(events: Iterable[ArchiveEvent]) -> CheckResult:
    check = "odds_archive.sportsbook_identity_preserved"
    bad: list[str] = []
    for event in events:
        for line in event.lines:
            sportsbook = line.get("sportsbook")
            if not isinstance(sportsbook, str) or not sportsbook.strip():
                bad.append(f"{_event_ref(event)}:blank_sportsbook")
        if len(set(event.sportsbooks)) != len(event.sportsbooks):
            bad.append(f"{_event_ref(event)}:duplicate_sportsbook")
    return finding(check, "P1", bad, "sportsbook identity problems")


def check_matched_opening_odds_present(
    mappings: Iterable[OddsGameMapping],
    lines_by_event: Mapping[tuple[date, int], Sequence[Mapping[str, Any]]],
) -> CheckResult:
    """MATCHED events feed canonical opening-market eval and need a full pair."""
    check = "odds_archive.matched_opening_odds_present"
    missing: list[str] = []
    for mapping in mappings:
        if mapping.status != MATCHED:
            continue
        lines = lines_by_event.get(mapping.event_id, ())
        has_pair = any(
            line.get("opening_home_american") is not None
            and _valid_american(line.get("opening_home_american"))
            and line.get("opening_away_american") is not None
            and _valid_american(line.get("opening_away_american"))
            for line in lines
        )
        if not has_pair:
            missing.append(_mapping_ref(mapping))
    return finding(check, "P1", missing, "MATCHED events without a full opening pair")


def check_home_away_orientation(
    mappings: Iterable[OddsGameMapping],
    candidates_by_pk: Mapping[int, GameCandidate],
) -> CheckResult:
    check = "odds_archive.home_away_orientation"
    bad: list[str] = []
    for mapping in mappings:
        if mapping.home_team_norm == mapping.away_team_norm:
            bad.append(f"{_mapping_ref(mapping)}:same_home_away")
            continue
        if mapping.status != MATCHED or mapping.game_pk is None:
            continue
        candidate = candidates_by_pk.get(mapping.game_pk)
        if candidate is None:
            bad.append(f"{_mapping_ref(mapping)}:missing_candidate")
            continue
        if (
            candidate.home_team_norm != mapping.home_team_norm
            or candidate.away_team_norm != mapping.away_team_norm
        ):
            bad.append(f"{_mapping_ref(mapping)}:orientation_mismatch")
    return finding(check, "P0", bad, "home/away orientation problems")


def check_team_name_normalization_deterministic(
    names: Iterable[str],
) -> CheckResult:
    check = "odds_archive.team_normalization_deterministic"
    bad: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        once = normalize_team_name(name)
        if normalize_team_name(once) != once or not once:
            bad.append(name)
    return finding(check, "P1", bad, "non-idempotent team-name normalizations")


def check_mapped_records_resolve(
    mappings: Iterable[OddsGameMapping],
    candidates_by_pk: Mapping[int, GameCandidate],
) -> CheckResult:
    """Every MATCHED record resolves to exactly one real, matching game_pk."""
    check = "odds_archive.mapped_records_resolve"
    bad: list[str] = []
    for mapping in mappings:
        if mapping.status != MATCHED:
            continue
        if mapping.game_pk is None:
            bad.append(f"{_mapping_ref(mapping)}:matched_without_game_pk")
            continue
        candidate = candidates_by_pk.get(mapping.game_pk)
        if candidate is None:
            bad.append(f"{_mapping_ref(mapping)}:game_pk_not_in_candidates")
            continue
        if (
            candidate.home_team_norm != mapping.home_team_norm
            or candidate.away_team_norm != mapping.away_team_norm
            or candidate.official_date != mapping.archive_date
        ):
            bad.append(f"{_mapping_ref(mapping)}:resolved_game_mismatch")
    return finding(check, "P0", bad, "MATCHED records that do not resolve cleanly")


def check_no_ambiguous_attachment(mappings: Iterable[OddsGameMapping]) -> CheckResult:
    """UNMATCHED/AMBIGUOUS carry no game_pk; MATCHED carries exactly one."""
    check = "odds_archive.no_ambiguous_attachment"
    bad: list[str] = []
    for mapping in mappings:
        if mapping.status == MATCHED:
            if mapping.game_pk is None:
                bad.append(f"{_mapping_ref(mapping)}:matched_missing_game_pk")
        elif mapping.status in (UNMATCHED, AMBIGUOUS):
            if mapping.game_pk is not None:
                bad.append(f"{_mapping_ref(mapping)}:{mapping.status}_attached_game_pk")
        else:
            bad.append(f"{_mapping_ref(mapping)}:unknown_status:{mapping.status}")
    return finding(check, "P0", bad, "illegal game_pk attachments")


def run_odds_archive_checks(
    records: Sequence[Mapping[str, Any]],
    events: Sequence[ArchiveEvent],
    mappings: Sequence[OddsGameMapping],
    candidates: Sequence[GameCandidate],
    *,
    artifact: Mapping[str, Any] | None = None,
    storage_root: str | Path | None = None,
) -> list[CheckResult]:
    """Run every DATA-009 check in a stable, deterministic order."""
    candidates_by_pk = {candidate.game_pk: candidate for candidate in candidates}
    lines_by_event = {event.event_id: event.lines for event in events}
    names = {event.home_team for event in events} | {event.away_team for event in events}
    results: list[CheckResult] = [check_published_sha256_recorded(artifact)]
    if storage_root is not None:
        results.append(check_source_file_immutability(artifact, storage_root))
    else:
        results.append(
            warn(
                "odds_archive.source_file_immutable",
                "storage_root not provided; source-file immutability not verified",
            )
        )
    results.extend(
        [
            check_moneylines_parse(records),
            check_sportsbook_identity_preserved(events),
            check_team_name_normalization_deterministic(sorted(names)),
            check_home_away_orientation(mappings, candidates_by_pk),
            check_mapped_records_resolve(mappings, candidates_by_pk),
            check_no_ambiguous_attachment(mappings),
            check_matched_opening_odds_present(mappings, lines_by_event),
        ]
    )
    return results


# --------------------------------------------------------------------------- #
# Coverage report (by season / date / sportsbook)
# --------------------------------------------------------------------------- #
def build_coverage_report(
    events: Sequence[ArchiveEvent], mappings: Sequence[OddsGameMapping]
) -> dict[str, Any]:
    """Deterministic coverage report exposing missingness and gaps.

    Keyed views by season, date, and sportsbook. Every bucket reports event
    status counts and opening-line presence so downstream MARKET-001 can see
    exactly which games/dates/books are usable for opening-market evaluation.
    """
    lines_by_event = {event.event_id: event.lines for event in events}
    by_season: dict[str, dict[str, int]] = {}
    by_date: dict[str, dict[str, int]] = {}
    by_sportsbook: dict[str, dict[str, int]] = {}
    totals = _empty_bucket()

    for mapping in sorted(mappings, key=lambda m: m.event_id):
        season = str(mapping.archive_date.year)
        day = mapping.archive_date.isoformat()
        season_bucket = by_season.setdefault(season, _empty_bucket())
        date_bucket = by_date.setdefault(day, _empty_bucket())
        lines = lines_by_event.get(mapping.event_id, ())
        for bucket in (totals, season_bucket, date_bucket):
            _tally_event(bucket, mapping, lines)

    for event in sorted(events, key=lambda e: e.event_id):
        for line in event.lines:
            book = str(line.get("sportsbook"))
            book_bucket = by_sportsbook.setdefault(book, _empty_sportsbook_bucket())
            book_bucket["lines"] += 1
            if line.get("opening_home_american") is not None and line.get(
                "opening_away_american"
            ) is not None:
                book_bucket["opening_pair_present"] += 1
            if line.get("current_home_american") is not None and line.get(
                "current_away_american"
            ) is not None:
                book_bucket["current_pair_present"] += 1

    return {
        "totals": totals,
        "by_season": dict(sorted(by_season.items())),
        "by_date": dict(sorted(by_date.items())),
        "by_sportsbook": dict(sorted(by_sportsbook.items())),
    }


def write_coverage_report(
    report: Mapping[str, Any], path: str | Path = DEFAULT_COVERAGE_REPORT
) -> Path:
    """Write the coverage report as deterministic, sorted JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return destination


def _empty_bucket() -> dict[str, int]:
    return {
        "events": 0,
        MATCHED: 0,
        UNMATCHED: 0,
        AMBIGUOUS: 0,
        "matched_with_opening_pair": 0,
        "sportsbook_lines": 0,
    }


def _empty_sportsbook_bucket() -> dict[str, int]:
    return {"lines": 0, "opening_pair_present": 0, "current_pair_present": 0}


def _tally_event(
    bucket: dict[str, int],
    mapping: OddsGameMapping,
    lines: Sequence[Mapping[str, Any]],
) -> None:
    bucket["events"] += 1
    bucket[mapping.status] = bucket.get(mapping.status, 0) + 1
    bucket["sportsbook_lines"] += len(lines)
    if mapping.status == MATCHED and any(
        line.get("opening_home_american") is not None
        and line.get("opening_away_american") is not None
        for line in lines
    ):
        bucket["matched_with_opening_pair"] += 1


# --------------------------------------------------------------------------- #
# DuckDB loaders + end-to-end orchestration
# --------------------------------------------------------------------------- #
def load_archive_artifact(connection: Any) -> dict[str, Any] | None:
    """Load the recorded archive artifact (published asset), if present."""
    if not _table_exists(connection, "bronze", "historical_odds_archive_artifacts"):
        return None
    row = connection.execute(
        """
        SELECT source, release_url, asset_name, payload_sha256, raw_path,
               first_archive_date, last_archive_date, game_count,
               moneyline_record_count
        FROM bronze.historical_odds_archive_artifacts
        WHERE payload_sha256 = ?
        """,
        [PUBLISHED_SHA256],
    ).fetchone()
    if row is None:
        return None
    keys = (
        "source",
        "release_url",
        "asset_name",
        "payload_sha256",
        "raw_path",
        "first_archive_date",
        "last_archive_date",
        "game_count",
        "moneyline_record_count",
    )
    return dict(zip(keys, row, strict=True))


def load_archive_records(connection: Any) -> list[dict[str, Any]]:
    """Load parsed archive moneyline records in deterministic order."""
    if not _table_exists(connection, "bronze", "historical_odds_moneylines"):
        return []
    rows = connection.execute(
        """
        SELECT archive_date, source_game_index, source_line_index, game_start_time,
               home_team, home_team_abbreviation, away_team, away_team_abbreviation,
               sportsbook, opening_home_american, opening_away_american,
               current_home_american, current_away_american
        FROM bronze.historical_odds_moneylines
        WHERE payload_sha256 = ?
        ORDER BY archive_date, source_game_index, source_line_index
        """,
        [PUBLISHED_SHA256],
    ).fetchall()
    keys = (
        "archive_date",
        "source_game_index",
        "source_line_index",
        "game_start_time",
        "home_team",
        "home_team_abbreviation",
        "away_team",
        "away_team_abbreviation",
        "sportsbook",
        "opening_home_american",
        "opening_away_american",
        "current_home_american",
        "current_away_american",
    )
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(zip(keys, row, strict=True))
        record["game_start_time"] = _as_utc(record["game_start_time"])
        records.append(record)
    return records


def load_game_candidates(connection: Any) -> list[GameCandidate]:
    """Load MLB Silver games as name-normalized mapping candidates."""
    if not _table_exists(connection, "silver", "games"):
        return []
    rows = connection.execute(
        """
        SELECT game_pk, official_date, game_date, source_game_json
        FROM silver.games
        ORDER BY game_pk
        """
    ).fetchall()
    candidates: list[GameCandidate] = []
    for game_pk, official_date, game_date, source_game_json in rows:
        home_norm, away_norm = _candidate_team_names(source_game_json)
        candidates.append(
            GameCandidate(
                game_pk=int(game_pk),
                official_date=_as_date(official_date),
                start_time=_as_utc(game_date),
                home_team_norm=home_norm,
                away_team_norm=away_norm,
            )
        )
    return candidates


def validate_odds_archive(
    connection: Any, storage_root: str | Path | None = None
) -> dict[str, Any]:
    """Load, map, and validate the archive; return a MARKET-001-ready result.

    The returned dict is deterministic and JSON-serializable (via ``default=str``
    for dates): ``results`` (CheckResult list), ``summary`` (see
    ``validation.results.summarize``), ``mappings``, and ``coverage``.
    """
    from validation.results import summarize

    artifact = load_archive_artifact(connection)
    records = load_archive_records(connection)
    candidates = load_game_candidates(connection)
    events = build_archive_events(records)
    mappings = map_archive_events(events, candidates)
    results = run_odds_archive_checks(
        records,
        events,
        mappings,
        candidates,
        artifact=artifact,
        storage_root=storage_root,
    )
    return {
        "results": results,
        "summary": summarize(results),
        "mappings": mappings,
        "coverage": build_coverage_report(events, mappings),
    }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _valid_american(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return abs(value) >= 100 and _MIN_INTEGER <= value <= _MAX_INTEGER


def _candidate_team_names(source_game_json: object) -> tuple[str | None, str | None]:
    payload = source_game_json
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return None, None
    if not isinstance(payload, dict):
        return None, None
    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return None, None
    return _side_name(teams.get("home")), _side_name(teams.get("away"))


def _side_name(side: object) -> str | None:
    if not isinstance(side, dict):
        return None
    team = side.get("team")
    if not isinstance(team, dict):
        return None
    name = team.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return normalize_team_name(name)


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value)!r}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"expected date, got {type(value)!r}")


def _table_exists(connection: Any, schema: str, table: str) -> bool:
    return bool(
        connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()[0]
    )


def _record_ref(record: Mapping[str, Any]) -> str:
    return (
        f"{record.get('archive_date')}#{record.get('source_game_index')}"
        f".{record.get('source_line_index')}"
    )


def _event_ref(event: ArchiveEvent) -> str:
    return f"{event.archive_date.isoformat()}#{event.source_game_index}"


def _mapping_ref(mapping: OddsGameMapping) -> str:
    return f"{mapping.archive_date.isoformat()}#{mapping.source_game_index}"
