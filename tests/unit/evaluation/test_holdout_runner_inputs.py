"""Regression tests for the ML-010 holdout runner input loader."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from holdout_2026 import _load_inputs  # noqa: E402


def test_load_inputs_excludes_future_scheduled_2026_games(tmp_path: Path) -> None:
    """The 2026 schedule contains future regular-season rows. Those rows are
    certified as schedule state, but they are not evaluable holdout outcomes and
    have no actual pitcher appearances yet. The runner must filter them before
    Gold build rather than failing on missing starter/bullpen features.
    """
    database = tmp_path / "mlb.duckdb"
    certification = tmp_path / "certification-PASS-test.json"
    certification.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE SCHEMA silver;
            CREATE TABLE silver.games (
                game_pk BIGINT,
                season VARCHAR,
                game_type VARCHAR,
                game_date TIMESTAMP,
                home_team_id BIGINT,
                away_team_id BIGINT,
                game_number INTEGER,
                abstract_game_state VARCHAR,
                detailed_state VARCHAR
            );
            CREATE TABLE silver.team_game_statistics (
                game_pk BIGINT,
                team_id BIGINT,
                side VARCHAR,
                score INTEGER,
                is_winner BOOLEAN,
                game_date TIMESTAMP
            );
            CREATE TABLE silver.pitcher_appearances (
                game_pk BIGINT,
                team_id BIGINT,
                side VARCHAR,
                pitcher_id BIGINT,
                appearance_order INTEGER,
                is_actual_starter BOOLEAN,
                outs_recorded INTEGER,
                batters_faced INTEGER,
                pitches_thrown INTEGER,
                earned_runs INTEGER,
                hits_allowed INTEGER,
                walks INTEGER,
                strikeouts INTEGER,
                home_runs_allowed INTEGER
            );
            CREATE TABLE silver.pitcher_starters (
                game_pk BIGINT,
                team_id BIGINT,
                side VARCHAR,
                actual_pitcher_id BIGINT,
                probable_pitcher_id BIGINT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO silver.games VALUES
              (1, '2026', 'R', TIMESTAMP '2026-08-11 19:00:00', 10, 20, 1, 'Final', 'Final'),
              (2, '2026', 'R', TIMESTAMP '2026-08-15 19:00:00', 30, 40, 1, 'Preview', 'Scheduled'),
              (3, '2026', 'R', TIMESTAMP '2026-08-12 19:00:00', 50, 60, 1, 'Final', 'Cancelled'),
              (4, '2026', 'S', TIMESTAMP '2026-03-01 19:00:00', 70, 80, 1, 'Final', 'Final');
            INSERT INTO silver.team_game_statistics VALUES
              (1, 10, 'home', 5, true, TIMESTAMP '2026-08-11 19:00:00'),
              (1, 20, 'away', 3, false, TIMESTAMP '2026-08-11 19:00:00'),
              (2, 30, 'home', NULL, NULL, TIMESTAMP '2026-08-15 19:00:00'),
              (2, 40, 'away', NULL, NULL, TIMESTAMP '2026-08-15 19:00:00'),
              (3, 50, 'home', NULL, NULL, TIMESTAMP '2026-08-12 19:00:00'),
              (3, 60, 'away', NULL, NULL, TIMESTAMP '2026-08-12 19:00:00'),
              (4, 70, 'home', 1, true, TIMESTAMP '2026-03-01 19:00:00'),
              (4, 80, 'away', 0, false, TIMESTAMP '2026-03-01 19:00:00');
            INSERT INTO silver.pitcher_appearances VALUES
              (1, 10, 'home', 100, 1, true, 18, 22, 80, 2, 5, 1, 6, 1),
              (1, 20, 'away', 200, 1, true, 15, 21, 75, 4, 7, 2, 5, 2);
            INSERT INTO silver.pitcher_starters VALUES
              (1, 10, 'home', 100, 100),
              (1, 20, 'away', 200, 200),
              (2, 30, 'home', NULL, NULL),
              (2, 40, 'away', NULL, NULL);
            """
        )

    inputs = _load_inputs(str(database), certification)

    assert [row["game_pk"] for row in inputs["games"]] == [1]
    assert {row["game_pk"] for row in inputs["team_stats"]} == {1}
    assert {row["game_pk"] for row in inputs["appearances"]} == {1}
    assert {row["game_pk"] for row in inputs["starters"]} == {1}
