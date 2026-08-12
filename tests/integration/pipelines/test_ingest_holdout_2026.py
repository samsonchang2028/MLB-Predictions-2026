"""Integration tests for the ML-010 2026 holdout ingestion script.

Mirrors ``tests/integration/pipelines/test_certify_historical.py``'s fixture
style (filtered-feed shaped schedule/detail payloads, no network), but proves
the opposite invariant: this is the one script that DOES accept season 2026,
scoped exclusively to it.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from ingest_holdout_2026 import run_holdout_ingestion  # noqa: E402

_OBSERVED_AT = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def _team(team_id: int, name: str, score: int, is_winner: bool) -> dict:
    return {"team": {"id": team_id, "name": name}, "score": score, "isWinner": is_winner}


def _game(game_pk: int, official_date: str, hour: int, home_id: int, home: str,
          away_id: int, away: str, home_score: int, away_score: int) -> dict:
    home_won = home_score > away_score
    return {
        "gamePk": game_pk,
        "season": "2026",
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
    _game(200, "2026-04-10", 23, 147, "New York Yankees", 111, "Boston Red Sox", 6, 2),
]


def _schedule_payload() -> bytes:
    return json.dumps({"dates": [{"date": "2026-01-01", "games": _GAMES}]}).encode()


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


_DETAILS = {200: _detail_payload(200, 147, 111)}


def _schedule_fetcher(_request) -> bytes:
    return _schedule_payload()


def _game_detail_fetcher(endpoint: str, _request):
    game_pk = int(endpoint.split("/")[4])
    return _DETAILS.get(game_pk)


def test_ingests_and_certifies_2026_season(tmp_path: Path) -> None:
    result = run_holdout_ingestion(
        tmp_path / "data",
        _schedule_fetcher,
        _game_detail_fetcher,
        run_id="test-holdout-2026",
        certifications_dir=tmp_path / "certs",
        logger=lambda _msg: None,
    )

    assert result["season"] == 2026
    assert result["backfill"]["fetched"] == 1
    artifact_path = Path(result["certification_path"])
    assert artifact_path.is_file()
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["status"] == result["certification_status"]


def test_refuses_a_non_2026_season(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only the 2026 holdout season"):
        run_holdout_ingestion(
            tmp_path / "data",
            _schedule_fetcher,
            _game_detail_fetcher,
            season=2025,
            logger=lambda _msg: None,
        )
