"""Unit tests for DATA-009 odds-archive validation checks (CheckResult contract)."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

from ingestion.odds import PUBLISHED_SHA256
from validation.odds_mapping import (
    MATCHED,
    UNMATCHED,
    GameCandidate,
    OddsGameMapping,
    build_archive_events,
    check_home_away_orientation,
    check_mapped_records_resolve,
    check_matched_opening_odds_present,
    check_moneylines_parse,
    check_no_ambiguous_attachment,
    check_published_sha256_recorded,
    check_source_file_immutability,
    check_sportsbook_identity_preserved,
    check_team_name_normalization_deterministic,
    normalize_team_name,
)


def _utc(hour: int) -> datetime:
    return datetime(2023, 5, 1, hour, tzinfo=timezone.utc)


def _candidate(game_pk: int, home: str, away: str) -> GameCandidate:
    return GameCandidate(
        game_pk=game_pk,
        official_date=date(2023, 5, 1),
        start_time=_utc(23),
        home_team_norm=normalize_team_name(home),
        away_team_norm=normalize_team_name(away),
    )


def _mapping(status: str, game_pk: int | None, home: str, away: str) -> OddsGameMapping:
    return OddsGameMapping(
        archive_date=date(2023, 5, 1),
        source_game_index=0,
        game_start_time=_utc(23),
        home_team=home,
        away_team=away,
        home_team_norm=normalize_team_name(home),
        away_team_norm=normalize_team_name(away),
        status=status,
        reason="test",
        game_pk=game_pk,
        candidate_count=1 if game_pk else 0,
        candidate_game_pks=(game_pk,) if game_pk else (),
        resolved_by="date_team_unique" if game_pk else None,
        sportsbooks=("A",),
    )


# --------------------------------------------------------------------------- #
# Provenance / immutability
# --------------------------------------------------------------------------- #
def test_published_sha256_recorded_pass_and_fail() -> None:
    assert check_published_sha256_recorded(
        {"payload_sha256": PUBLISHED_SHA256}
    ).status == "PASS"
    assert check_published_sha256_recorded({"payload_sha256": "deadbeef"}).status == "FAIL"
    assert check_published_sha256_recorded(None).status == "FAIL"


def test_source_file_immutability(tmp_path: Path) -> None:
    payload = b"archive bytes"
    digest = hashlib.sha256(payload).hexdigest()
    raw_dir = tmp_path / "raw" / "odds"
    raw_dir.mkdir(parents=True)
    (raw_dir / "archive.json").write_bytes(payload)
    artifact = {"payload_sha256": digest, "raw_path": "odds/archive.json"}

    assert check_source_file_immutability(artifact, tmp_path).status == "PASS"

    # Mutated file is caught.
    (raw_dir / "archive.json").write_bytes(b"tampered")
    result = check_source_file_immutability(artifact, tmp_path)
    assert result.status == "FAIL"
    assert result.severity == "P0"

    # Missing file is caught.
    missing = {"payload_sha256": digest, "raw_path": "odds/gone.json"}
    assert check_source_file_immutability(missing, tmp_path).status == "FAIL"


# --------------------------------------------------------------------------- #
# Content validity
# --------------------------------------------------------------------------- #
def test_moneylines_parse_flags_invalid_prices() -> None:
    good = {
        "archive_date": date(2023, 5, 1), "source_game_index": 0, "source_line_index": 0,
        "opening_home_american": -120, "opening_away_american": 110,
        "current_home_american": None, "current_away_american": 105,
    }
    bad = {
        "archive_date": date(2023, 5, 1), "source_game_index": 0, "source_line_index": 1,
        "opening_home_american": 50,  # |value| < 100 is not valid American odds
        "opening_away_american": 110,
        "current_home_american": 105, "current_away_american": 105,
    }
    assert check_moneylines_parse([good]).status == "PASS"
    assert check_moneylines_parse([good, bad]).status == "FAIL"


def test_sportsbook_identity_preserved() -> None:
    day = date(2023, 5, 1)
    lines = [
        {"archive_date": day, "source_game_index": 0, "source_line_index": 0,
         "game_start_time": _utc(23), "home_team": "Yankees", "away_team": "Red Sox",
         "sportsbook": "DraftKings", "opening_home_american": -120,
         "opening_away_american": 110, "current_home_american": None,
         "current_away_american": None},
    ]
    (event,) = build_archive_events(lines)
    assert check_sportsbook_identity_preserved([event]).status == "PASS"


def test_team_normalization_check_passes_on_real_names() -> None:
    result = check_team_name_normalization_deterministic(
        ["New York Yankees", "St. Louis Cardinals", "Montréal Expos"]
    )
    assert result.status == "PASS"


# --------------------------------------------------------------------------- #
# Mapping-integrity checks
# --------------------------------------------------------------------------- #
def test_orientation_regression_flags_swapped_matched_game() -> None:
    # A MATCHED mapping pointing at a game whose home/away are reversed must fail.
    candidates_by_pk = {1: _candidate(1, "Red Sox", "Yankees")}  # reversed vs mapping
    swapped = _mapping(MATCHED, 1, "Yankees", "Red Sox")
    assert check_home_away_orientation([swapped], candidates_by_pk).status == "FAIL"

    aligned = {1: _candidate(1, "Yankees", "Red Sox")}
    ok_mapping = _mapping(MATCHED, 1, "Yankees", "Red Sox")
    assert check_home_away_orientation([ok_mapping], aligned).status == "PASS"


def test_mapped_records_resolve_requires_real_matching_game() -> None:
    aligned = {1: _candidate(1, "Yankees", "Red Sox")}
    good = _mapping(MATCHED, 1, "Yankees", "Red Sox")
    assert check_mapped_records_resolve([good], aligned).status == "PASS"

    dangling = _mapping(MATCHED, 999, "Yankees", "Red Sox")
    assert check_mapped_records_resolve([dangling], aligned).status == "FAIL"


def test_no_ambiguous_attachment_enforced() -> None:
    good = [
        _mapping(MATCHED, 1, "Yankees", "Red Sox"),
        _mapping(UNMATCHED, None, "Cubs", "Reds"),
    ]
    assert check_no_ambiguous_attachment(good).status == "PASS"

    illegal = OddsGameMapping(
        archive_date=date(2023, 5, 1), source_game_index=0, game_start_time=_utc(23),
        home_team="Cubs", away_team="Reds", home_team_norm="cubs", away_team_norm="reds",
        status="AMBIGUOUS", reason="test", game_pk=7, candidate_count=2,
        candidate_game_pks=(7, 8), resolved_by=None, sportsbooks=("A",),
    )
    assert check_no_ambiguous_attachment([illegal]).status == "FAIL"


def test_matched_opening_odds_present() -> None:
    day = date(2023, 5, 1)
    event_id = (day, 0)
    present = {event_id: [{"opening_home_american": -120, "opening_away_american": 110}]}
    missing = {event_id: [{"opening_home_american": None, "opening_away_american": 110}]}
    mapping = _mapping(MATCHED, 1, "Yankees", "Red Sox")
    assert check_matched_opening_odds_present([mapping], present).status == "PASS"
    assert check_matched_opening_odds_present([mapping], missing).status == "FAIL"
