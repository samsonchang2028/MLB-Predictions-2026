import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ingestion.mlb import backfill_game_details, ingest_schedule
from ingestion.odds import ingest_the_odds_api_moneylines
from storage import connect_database, initialize_storage
from transforms import normalize_silver


ODDS_FIXTURE = (
    Path(__file__).parents[2]
    / "unit"
    / "ingestion"
    / "odds"
    / "fixtures"
    / "the_odds_api_moneylines.json"
)


def _schedule_payload() -> bytes:
    game = {
        "gamePk": 777001,
        "season": "2026",
        "gameType": "R",
        "gameDate": "2026-04-01T20:10:00Z",
        "officialDate": "2026-04-01",
        "teams": {
            "away": {
                "team": {"id": 119, "name": "Los Angeles Dodgers"},
                "score": 3,
                "isWinner": False,
            },
            "home": {
                "team": {"id": 137, "name": "San Francisco Giants"},
                "score": 4,
                "isWinner": True,
            },
        },
        "status": {
            "abstractGameState": "Final",
            "codedGameState": "F",
            "detailedState": "Final",
            "statusCode": "F",
        },
    }
    return json.dumps({"dates": [{"date": "2026-04-01", "games": [game]}]}).encode()


def _game_detail_payload() -> bytes:
    def player(pitcher_id: int, innings: str, pitches: int) -> dict[str, object]:
        return {
            "person": {"id": pitcher_id},
            "stats": {
                "pitching": {
                    "inningsPitched": innings,
                    "outs": 3,
                    "battersFaced": 4,
                    "numberOfPitches": pitches,
                    "strikes": pitches - 8,
                    "balls": 8,
                    "hits": 1,
                    "runs": 0,
                    "earnedRuns": 0,
                    "baseOnBalls": 0,
                    "strikeOuts": 2,
                    "homeRuns": 0,
                }
            },
        }

    payload = {
        "gamePk": 777001,
        "gameData": {
            "probablePitchers": {
                "home": {"id": 9001},
                "away": {"id": 9002},
            }
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "home": {
                        "team": {"id": 137},
                        "pitchers": [9101, 9102],
                        "players": {
                            "ID9101": player(9101, "6.0", 90),
                            "ID9102": player(9102, "1.0", 18),
                        },
                    },
                    "away": {
                        "team": {"id": 119},
                        "pitchers": [9201],
                        "players": {"ID9201": player(9201, "7.0", 95)},
                    },
                }
            }
        },
    }
    return json.dumps(payload).encode()


def _rows(connection: object, table: str) -> list[tuple[object, ...]]:
    return connection.execute(f"SELECT * FROM silver.{table} ORDER BY ALL").fetchall()


def test_ingested_bronze_normalizes_deterministically_with_timestamp_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    paths = initialize_storage(root)
    observed_at = datetime(2026, 3, 31, 9, 30, tzinfo=timezone.utc)
    odds_payload = json.loads(ODDS_FIXTURE.read_text(encoding="utf-8"))

    ingest_schedule(
        root,
        lambda _: _schedule_payload(),
        season=2026,
        fetched_at=observed_at,
    )
    detail_result = backfill_game_details(
        root,
        lambda *_: _game_detail_payload(),
        game_pks=[777001],
        fetched_at=observed_at,
        run_id="integration-detail",
    )
    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, odds_payload)
        first_counts = normalize_silver(connection)
        first = {
            table: _rows(connection, table)
            for table in (
                "games",
                "team_game_statistics",
                "pitcher_appearances",
                "pitcher_starters",
                "odds_snapshots",
                "odds_event_game_mapping",
            )
        }
        second_counts = normalize_silver(connection)
        second = {table: _rows(connection, table) for table in first}
        game_source_time = connection.execute(
            "SELECT source_observed_at FROM silver.games"
        ).fetchone()[0]
        snapshot_time, commence_time = connection.execute(
            """
            SELECT snapshot_timestamp, commence_time FROM silver.odds_snapshots
            WHERE bookmaker = 'book_a' AND outcome = 'home'
            """
        ).fetchone()
        mapping = connection.execute(
            "SELECT mapping_status, game_pk FROM silver.odds_event_game_mapping"
        ).fetchone()
        appearances = connection.execute(
            """SELECT side, pitcher_id, appearance_order, is_actual_starter,
                      source_payload_sha256
               FROM silver.pitcher_appearances ORDER BY side, appearance_order"""
        ).fetchall()
        starters = connection.execute(
            """SELECT side, actual_pitcher_id, probable_pitcher_id
               FROM silver.pitcher_starters ORDER BY side"""
        ).fetchall()
        detail_sha = connection.execute(
            "SELECT payload_sha256 FROM bronze.mlb_game_detail_payloads"
        ).fetchone()[0]

    assert first_counts == second_counts
    assert first == second
    assert game_source_time == observed_at.replace(tzinfo=None)
    assert snapshot_time == datetime(2026, 4, 1, 18, tzinfo=timezone.utc)
    assert commence_time == datetime(2026, 4, 1, 20, 10, tzinfo=timezone.utc)
    assert mapping == ("mapped", 777001)
    assert detail_result["fetched"] == 1
    assert first_counts["pitcher_appearances"] == 3
    assert appearances == [
        ("away", 9201, 1, True, detail_sha),
        ("home", 9101, 1, True, detail_sha),
        ("home", 9102, 2, False, detail_sha),
    ]
    assert starters == [("away", 9201, 9002), ("home", 9101, 9001)]


def test_new_odds_snapshot_does_not_replace_earlier_observation(tmp_path: Path) -> None:
    root = tmp_path / "data"
    paths = initialize_storage(root)
    payload = json.loads(ODDS_FIXTURE.read_text(encoding="utf-8"))
    later = deepcopy(payload)
    later[0]["bookmakers"] = [later[0]["bookmakers"][0]]
    later[0]["bookmakers"][0]["last_update"] = "2026-04-01T19:00:00Z"
    later[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 130

    ingest_schedule(
        root,
        lambda _: _schedule_payload(),
        season=2026,
        fetched_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    with connect_database(paths["database"]) as connection:
        ingest_the_odds_api_moneylines(connection, payload)
        ingest_the_odds_api_moneylines(connection, later)
        normalize_silver(connection)
        home_prices = connection.execute(
            """
            SELECT american_price FROM silver.odds_snapshots
            WHERE bookmaker = 'book_a' AND outcome = 'home'
            ORDER BY snapshot_timestamp
            """
        ).fetchall()

    assert home_prices == [(125,), (130,)]
