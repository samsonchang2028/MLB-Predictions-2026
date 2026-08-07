"""Integration tests for the DATA-011 historical certification runner.

Exercises the full wiring (schedule -> backfill -> normalize -> odds -> mapping
-> certification) with injected fixture fetchers and a fixture odds-ingest, so no
network and no 80MB archive are required. The odds-ingest fixture reuses the
DATA-008-shaped Bronze layout that DATA-009 validates.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ingestion.odds import PUBLISHED_SHA256
from pipelines import run_historical_certification
from storage import connect_database, storage_paths

_OBSERVED_AT = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# MLB schedule + game-detail fixtures (filtered-feed shaped)
# --------------------------------------------------------------------------- #
def _team(team_id: int, name: str, score: int, is_winner: bool) -> dict:
    return {"team": {"id": team_id, "name": name}, "score": score, "isWinner": is_winner}


def _game(game_pk: int, official_date: str, hour: int, home_id: int, home: str,
          away_id: int, away: str, home_score: int, away_score: int) -> dict:
    home_won = home_score > away_score
    return {
        "gamePk": game_pk,
        "season": "2024",
        "gameType": "R",
        "gameDate": f"{official_date}T{hour:02d}:10:00Z",
        "officialDate": official_date,
        "doubleHeader": "N",
        "gameNumber": 1,
        "teams": {
            "home": _team(home_id, home, home_score, home_won),
            "away": _team(away_id, away, away_score, not home_won),
        },
        "status": {
            "abstractGameState": "Final", "detailedState": "Final",
            "codedGameState": "F", "statusCode": "F",
        },
    }


_GAMES = [
    _game(100, "2024-04-10", 23, 147, "New York Yankees", 111, "Boston Red Sox", 5, 3),
    _game(101, "2024-04-11", 23, 112, "Chicago Cubs", 113, "Cincinnati Reds", 2, 4),
]


def _schedule_payload() -> bytes:
    return json.dumps({"dates": [{"date": "2024-01-01", "games": _GAMES}]}).encode()


def _detail_payload(game_pk: int, home_id: int, away_id: int) -> bytes:
    home_pid, away_pid = game_pk * 10 + 1, game_pk * 10 + 2

    def player(pitcher_id: int) -> dict:
        return {
            "person": {"id": pitcher_id},
            "stats": {"pitching": {
                "inningsPitched": "6.0", "outs": 18, "battersFaced": 24,
                "numberOfPitches": 90, "strikes": 60, "balls": 30, "hits": 5,
                "runs": 2, "earnedRuns": 2, "baseOnBalls": 1, "strikeOuts": 7,
                "homeRuns": 1,
            }},
        }

    payload = {
        "gamePk": game_pk,
        "gameData": {"probablePitchers": {"home": {"id": home_pid}, "away": {"id": away_pid}}},
        "liveData": {"boxscore": {"teams": {
            "home": {"team": {"id": home_id}, "pitchers": [home_pid],
                     "players": {f"ID{home_pid}": player(home_pid)}},
            "away": {"team": {"id": away_id}, "pitchers": [away_pid],
                     "players": {f"ID{away_pid}": player(away_pid)}},
        }}},
    }
    return json.dumps(payload).encode()


_DETAILS = {
    100: _detail_payload(100, 147, 111),
    101: _detail_payload(101, 112, 113),
}


def _schedule_fetcher(_request) -> bytes:
    return _schedule_payload()


def _game_detail_fetcher(endpoint: str, _request):
    game_pk = int(endpoint.split("/")[4])
    return _DETAILS.get(game_pk)


# --------------------------------------------------------------------------- #
# Fixture odds-ingest (idempotent DATA-008-shaped Bronze seed)
# --------------------------------------------------------------------------- #
def _seed_historical_odds(storage_root, _odds_archive_path) -> dict:
    database = storage_paths(storage_root)["database"]
    with connect_database(database) as connection:
        connection.execute(
            """
            CREATE SCHEMA IF NOT EXISTS bronze;
            CREATE TABLE IF NOT EXISTS bronze.historical_odds_archive_artifacts (
                source VARCHAR NOT NULL, release_url VARCHAR NOT NULL,
                asset_name VARCHAR NOT NULL, payload_sha256 VARCHAR PRIMARY KEY,
                raw_path VARCHAR NOT NULL UNIQUE, first_archive_date DATE NOT NULL,
                last_archive_date DATE NOT NULL, game_count INTEGER NOT NULL,
                moneyline_record_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bronze.historical_odds_moneylines (
                payload_sha256 VARCHAR NOT NULL, archive_date DATE NOT NULL,
                source_game_index INTEGER NOT NULL, source_line_index INTEGER NOT NULL,
                game_start_time TIMESTAMPTZ NOT NULL, home_team VARCHAR NOT NULL,
                home_team_abbreviation VARCHAR NOT NULL, away_team VARCHAR NOT NULL,
                away_team_abbreviation VARCHAR NOT NULL, sportsbook VARCHAR NOT NULL,
                opening_home_american INTEGER, opening_away_american INTEGER,
                current_home_american INTEGER, current_away_american INTEGER,
                PRIMARY KEY (payload_sha256, archive_date, source_game_index, source_line_index)
            )
            """
        )
        rows = [
            (PUBLISHED_SHA256, date(2024, 4, 10), 0, 0,
             datetime.fromisoformat("2024-04-10T23:10:00+00:00"),
             "New York Yankees", "NYY", "Boston Red Sox", "BOS", "DraftKings",
             -120, 110, -115, 105),
            (PUBLISHED_SHA256, date(2024, 4, 10), 0, 1,
             datetime.fromisoformat("2024-04-10T23:10:00+00:00"),
             "New York Yankees", "NYY", "Boston Red Sox", "BOS", "FanDuel",
             -118, 108, -110, 100),
        ]
        connection.executemany(
            """INSERT INTO bronze.historical_odds_moneylines VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            rows,
        )
        connection.execute(
            """INSERT INTO bronze.historical_odds_archive_artifacts VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            [
                "arnavsaraogi_mlb_odds_archive", "https://example/release",
                "mlb_odds_dataset.json", PUBLISHED_SHA256,
                "odds/historical_archive/3f/archive.json",
                date(2024, 4, 10), date(2024, 4, 10), 1, len(rows),
            ],
        )
    return {"records_inserted": 2}


def _run(root: Path, *, retry_unresolved: bool = False) -> dict:
    return run_historical_certification(
        root,
        "unused-odds-path.json",
        _schedule_fetcher,
        _game_detail_fetcher,
        seasons=(2024,),
        run_id="test-historical",
        certifications_dir=root / "certs",
        retry_unresolved=retry_unresolved,
        odds_ingest=_seed_historical_odds,
        logger=lambda _msg: None,
    )


def test_runner_produces_pass_certification_and_coverage(tmp_path: Path) -> None:
    result = _run(tmp_path / "data")

    assert result["certification_status"] == "PASS"
    artifact_path = Path(result["certification_path"])
    assert artifact_path.is_file()
    assert "PASS" in artifact_path.name
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["status"] == "PASS"
    assert saved["merge_blocking"] == []

    # Both game details were fetched and both games certified.
    assert result["backfill"]["fetched"] == 2
    assert result["certification"]["row_counts"]

    # Odds coverage is non-empty and the single game maps MATCHED.
    coverage = result["odds_coverage"]
    assert coverage["totals"]["events"] == 1
    assert coverage["totals"]["MATCHED"] == 1


def test_runner_refuses_holdout_season(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="holdout"):
        run_historical_certification(
            tmp_path / "data", "x.json", _schedule_fetcher, _game_detail_fetcher,
            seasons=(2025, 2026), odds_ingest=_seed_historical_odds,
            logger=lambda _m: None,
        )


def test_tampered_raw_payload_forces_fail_certification(tmp_path: Path) -> None:
    root = tmp_path / "data"
    first = _run(root)
    assert first["certification_status"] == "PASS"

    # Tamper a stored raw game-detail payload on disk.
    raw_files = sorted((root / "raw" / "mlb" / "game-details").rglob("*.json"))
    assert raw_files
    raw_files[0].write_bytes(b'{"gamePk": 100, "tampered": true}')

    # Re-run: DATA-010 isolates the corrupt game_pk (records failed, continues),
    # and the DATA-006 Bronze-integrity check forces a FAIL certification.
    second = _run(root, retry_unresolved=False)
    assert second["certification_status"] == "FAIL"
    assert "FAIL" in Path(second["certification_path"]).name
    assert "bronze.detail_payload_integrity" in second["certification"]["merge_blocking"]
