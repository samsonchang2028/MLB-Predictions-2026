import hashlib
from pathlib import Path

import pytest

import ingestion.odds.historical as historical
from ingestion.odds import HistoricalOddsArchiveError
from storage import connect_database, storage_paths


FIXTURE = (
    Path(__file__).parents[3]
    / "unit"
    / "ingestion"
    / "odds"
    / "fixtures"
    / "historical_odds_archive.json"
)


def allow_fixture_checksum(monkeypatch: pytest.MonkeyPatch) -> str:
    checksum = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    monkeypatch.setattr(historical, "PUBLISHED_SHA256", checksum)
    return checksum


def test_retains_source_and_reingests_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checksum = allow_fixture_checksum(monkeypatch)

    first = historical.ingest_historical_odds_archive(tmp_path, FIXTURE)
    second = historical.ingest_historical_odds_archive(tmp_path, FIXTURE)

    assert first["payload_sha256"] == checksum
    assert first["records_seen"] == 2
    assert first["records_inserted"] == 2
    assert second["records_inserted"] == 0
    assert first["raw_path"] == second["raw_path"]
    assert first["raw_path"].read_bytes() == FIXTURE.read_bytes()

    with connect_database(storage_paths(tmp_path)["database"]) as connection:
        artifact = connection.execute(
            """
            SELECT source, release_url, asset_name, payload_sha256, raw_path,
                   first_archive_date, last_archive_date, game_count,
                   moneyline_record_count
            FROM bronze.historical_odds_archive_artifacts
            """
        ).fetchone()
        rows = connection.execute(
            """
            SELECT sportsbook, home_team, away_team,
                   opening_home_american, opening_away_american,
                   current_home_american, current_away_american
            FROM bronze.historical_odds_moneylines
            ORDER BY source_line_index
            """
        ).fetchall()
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'bronze'
                    AND table_name = 'historical_odds_moneylines'
                """
            ).fetchall()
        }

    assert artifact[:4] == (
        historical.ARCHIVE_SOURCE,
        historical.ARCHIVE_RELEASE_URL,
        historical.ARCHIVE_ASSET_NAME,
        checksum,
    )
    assert artifact[4] == f"odds/historical_archive/{checksum[:2]}/{checksum}.json"
    assert artifact[7:] == (1, 2)
    assert rows == [
        (
            "fanduel",
            "New York Yankees",
            "Toronto Blue Jays",
            -188,
            155,
            -200,
            168,
        ),
        (
            "caesars",
            "New York Yankees",
            "Toronto Blue Jays",
            -162,
            None,
            None,
            170,
        ),
    ]
    assert "snapshot_timestamp" not in columns
    assert "observation_timestamp" not in columns


def test_checksum_mismatch_fails_before_retention(tmp_path: Path) -> None:
    with pytest.raises(HistoricalOddsArchiveError, match="SHA-256 mismatch"):
        historical.ingest_historical_odds_archive(tmp_path, FIXTURE)

    assert not (tmp_path / "raw").exists()


def test_retained_archive_cannot_be_silently_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow_fixture_checksum(monkeypatch)
    result = historical.ingest_historical_odds_archive(tmp_path, FIXTURE)
    result["raw_path"].write_bytes(b"tampered")

    with pytest.raises(HistoricalOddsArchiveError, match="retained historical"):
        historical.ingest_historical_odds_archive(tmp_path, FIXTURE)
