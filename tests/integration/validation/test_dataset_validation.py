"""Integration tests: run validation against small real Bronze/Silver fixtures.

Fixtures are built with the actual ingestion + normalization code (no network),
covering a regular Final game, a same-day doubleheader, a postponed game, and a
spring-training game whose pitcher appearances must be flagged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ingestion.mlb import backfill_game_details, ingest_schedule
from ingestion.odds import ingest_the_odds_api_moneylines
from storage import connect_database, initialize_storage
from transforms import normalize_silver
from validation import certify
from validation.checks import check_home_win_derivation
from validation.runner import run_all

ODDS_FIXTURE = (
    Path(__file__).parents[2]
    / "unit"
    / "ingestion"
    / "odds"
    / "fixtures"
    / "the_odds_api_moneylines.json"
)

_OBSERVED_AT = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)


def _team(team_id: int, name: str, score: int | None, is_winner: bool | None) -> dict:
    side: dict[str, object] = {"team": {"id": team_id, "name": name}}
    if score is not None:
        side["score"] = score
    if is_winner is not None:
        side["isWinner"] = is_winner
    return side


def _game(
    game_pk: int,
    official_date: str,
    hour: int,
    home_id: int,
    away_id: int,
    *,
    game_type: str = "R",
    abstract: str = "Final",
    detailed: str = "Final",
    coded: str = "F",
    status_code: str = "F",
    home_score: int | None,
    away_score: int | None,
    home_won: bool | None,
    double_header: str = "N",
    game_number: int = 1,
) -> dict:
    away_won = None if home_won is None else not home_won
    return {
        "gamePk": game_pk,
        "season": "2024",
        "gameType": game_type,
        "gameDate": f"{official_date}T{hour:02d}:10:00Z",
        "officialDate": official_date,
        "doubleHeader": double_header,
        "gameNumber": game_number,
        "teams": {
            "home": _team(home_id, f"Home {home_id}", home_score, home_won),
            "away": _team(away_id, f"Away {away_id}", away_score, away_won),
        },
        "status": {
            "abstractGameState": abstract,
            "detailedState": detailed,
            "codedGameState": coded,
            "statusCode": status_code,
        },
    }


def _schedule_payload() -> bytes:
    games = [
        # Regular Final, has detail.
        _game(100, "2024-04-10", 23, 111, 222, home_score=5, away_score=3, home_won=True),
        # Same-day doubleheader between the same teams (game_number 1 & 2).
        _game(200, "2024-05-01", 17, 111, 333, home_score=2, away_score=1, home_won=True,
              double_header="S", game_number=1),
        _game(201, "2024-05-01", 23, 111, 333, home_score=0, away_score=4, home_won=False,
              double_header="S", game_number=2),
        # Postponed: no scores/winner, non-Final.
        _game(300, "2024-04-11", 23, 111, 444, game_type="R", abstract="Preview",
              detailed="Postponed", coded="D", status_code="DR",
              home_score=None, away_score=None, home_won=None),
        # Spring-training Final, has detail -> non-regular-season appearances.
        _game(400, "2024-03-05", 20, 111, 555, game_type="S", home_score=2, away_score=1,
              home_won=True),
    ]
    return json.dumps({"dates": [{"date": "2024-01-01", "games": games}]}).encode()


def _detail_payload(game_pk: int, home_id: int, away_id: int, home_pid: int, away_pid: int) -> bytes:
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
    100: _detail_payload(100, 111, 222, 1101, 2201),
    400: _detail_payload(400, 111, 555, 4101, 5501),
}


def _build_fixture(root: Path) -> Path:
    paths = initialize_storage(root)
    ingest_schedule(root, lambda _: _schedule_payload(), season=2024, fetched_at=_OBSERVED_AT)
    backfill_game_details(
        root,
        lambda endpoint, _req: _DETAILS[int(endpoint.split("/")[4])],
        game_pks=list(_DETAILS),
        fetched_at=_OBSERVED_AT,
        run_id="test-run",
    )
    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(
            connection, json.loads(ODDS_FIXTURE.read_text(encoding="utf-8"))
        )
        normalize_silver(connection)
    return paths["database"]


def _results_by_check(results) -> dict:
    return {r.check: r for r in results}


def test_clean_fixture_certifies_pass_with_expected_warnings(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        results = run_all(connection, storage_root=root)
    by_check = _results_by_check(results)

    # No merge-blocking failures on clean data.
    blocking = [r.check for r in results if r.blocking]
    assert blocking == [], blocking

    # Spring-training appearances are surfaced as an advisory (DATA-005 P3c).
    assert by_check["pitching.non_regular_season"].status == "WARN"
    assert any(row[0] == 400 for row in by_check["pitching.non_regular_season"].failing)

    # Core identity/results/leakage checks pass.
    for name in (
        "identity.game_pk_unique",
        "identity.home_away_distinct",
        "identity.doubleheader_integrity",
        "results.home_win_derivation",
        "results.status_consistency",
        "ref.pitcher_appearance_game",
        "leakage.future_mutation_invariance",
        "leakage.current_game_excluded",
        "leakage.chronological_folds",
        "bronze.detail_payload_integrity",
        "bronze.processing_determinism",
    ):
        assert by_check[name].status == "PASS", name


def test_certify_summary_is_pass(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        summary = certify(connection, storage_root=root)
    assert summary["status"] == "PASS"
    assert summary["merge_blocking"] == []
    assert summary["warnings"] >= 1


def test_doubleheader_and_postponed_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        dh = connection.execute(
            """SELECT game_pk, game_number FROM silver.games
               WHERE official_date = '2024-05-01' ORDER BY game_pk"""
        ).fetchall()
        postponed_stats = connection.execute(
            "SELECT count(*) FROM silver.team_game_statistics WHERE game_pk = 300"
        ).fetchone()[0]
    # Both doubleheader games survive with distinct game numbers (not deduped).
    assert dh == [(200, 1), (201, 2)]
    # Postponed game carries no completed results.
    assert postponed_stats == 0


def test_tampered_raw_payload_blocks_certification(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    raw_files = sorted((root / "raw" / "mlb" / "game-details").rglob("*.json"))
    raw_files[0].write_bytes(b'{"gamePk": 100, "tampered": true}')

    with connect_database(database) as connection:
        summary = certify(connection, storage_root=root)
        results = run_all(connection, storage_root=root)
    detail = _results_by_check(results)["bronze.detail_payload_integrity"]

    assert detail.status == "FAIL"
    assert detail.blocking is True
    assert summary["status"] == "FAIL"
    assert "bronze.detail_payload_integrity" in summary["merge_blocking"]


_SILVER_TABLES = (
    "games",
    "team_game_statistics",
    "pitcher_appearances",
    "pitcher_starters",
    "odds_snapshots",
    "odds_event_game_mapping",
)


def _silver_snapshot(connection) -> dict:
    return {
        t: connection.execute(f"SELECT * FROM silver.{t} ORDER BY ALL").fetchall()
        for t in _SILVER_TABLES
    }


def test_certify_does_not_mutate_committed_silver(tmp_path: Path) -> None:
    """Regression (DATA-006 P1): certify()/run_all() must be side-effect-free.

    The determinism check previously called normalize_silver() on the live
    connection, rewriting the certified Silver tables (and masking drift). It
    must now rebuild in isolation and leave the committed dataset untouched.
    """
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        before = _silver_snapshot(connection)
        certify(connection, storage_root=root)
        run_all(connection, storage_root=root)
        after = _silver_snapshot(connection)
    assert before == after


def test_processing_determinism_detects_committed_drift(tmp_path: Path) -> None:
    """Regression (DATA-006 P1): drift between committed Silver and a Bronze
    rebuild must be reported, not silently overwritten by the check itself."""
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        # Corrupt the committed Silver so it no longer matches a Bronze rebuild.
        connection.execute(
            "UPDATE silver.team_game_statistics SET score = score + 99 "
            "WHERE game_pk = 100 AND side = 'home'"
        )
        results = run_all(connection, storage_root=root)
        # The tamper must remain visible afterwards (check did not rewrite it).
        tampered = connection.execute(
            "SELECT score FROM silver.team_game_statistics "
            "WHERE game_pk = 100 AND side = 'home'"
        ).fetchone()[0]
    determinism = _results_by_check(results)["bronze.processing_determinism"]
    assert determinism.status == "FAIL"
    assert determinism.blocking is True
    assert "team_game_statistics" in determinism.failing
    assert tampered >= 99


def test_inconsistent_home_win_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        # Flip the home winner flag on a Final game so score and is_winner disagree.
        connection.execute(
            "UPDATE silver.team_game_statistics SET is_winner = FALSE "
            "WHERE game_pk = 100 AND side = 'home'"
        )
        result = check_home_win_derivation(connection)
    assert result.status == "FAIL"
    assert result.blocking is True
    assert 100 in result.failing
