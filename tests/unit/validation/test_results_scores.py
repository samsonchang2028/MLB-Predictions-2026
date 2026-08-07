"""Regression tests for results.valid_scores (DATA-012).

A postponed/suspended/cancelled game can carry ``abstractGameState='Final'`` in
the MLB feed while legitimately having no score. Such a game must not be flagged,
but a genuinely completed game missing a score must still fail.
"""

from __future__ import annotations

import duckdb

from validation.checks import check_results_scores


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA silver")
    con.execute(
        """CREATE TABLE silver.games (
               game_pk BIGINT, abstract_game_state VARCHAR, detailed_state VARCHAR
           )"""
    )
    con.execute(
        """CREATE TABLE silver.team_game_statistics (
               game_pk BIGINT, side VARCHAR, score INTEGER, is_winner BOOLEAN
           )"""
    )
    return con


def test_postponed_game_with_final_abstract_state_is_not_flagged() -> None:
    con = _connection()
    con.execute(
        """INSERT INTO silver.games VALUES
               (1, 'Final', 'Final'),        -- completed, has scores
               (2, 'Final', 'Postponed'),    -- MLB quirk: abstract Final, no score
               (3, 'Final', 'Suspended: Rain'),
               (4, 'Final', 'Cancelled')"""
    )
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (1, 'home', 5, TRUE), (1, 'away', 3, FALSE),
               (2, 'home', NULL, NULL), (2, 'away', NULL, NULL),
               (3, 'home', NULL, NULL), (3, 'away', NULL, NULL),
               (4, 'home', NULL, NULL), (4, 'away', NULL, NULL)"""
    )
    result = check_results_scores(con)
    assert result.status == "PASS", (result.message, result.failing)


def test_completed_game_missing_score_is_still_flagged() -> None:
    con = _connection()
    con.execute(
        """INSERT INTO silver.games VALUES
               (10, 'Final', 'Final'),
               (11, 'Final', 'Postponed')"""
    )
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (10, 'home', NULL, NULL), (10, 'away', 2, TRUE),
               (11, 'home', NULL, NULL), (11, 'away', NULL, NULL)"""
    )
    result = check_results_scores(con)
    assert result.status == "FAIL"
    # The genuinely-broken completed game is flagged; the postponed one is not.
    flagged = {row[0] for row in result.failing}
    assert flagged == {10}


def test_negative_score_on_completed_game_is_flagged() -> None:
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (20, 'Final', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (20, 'home', -1, FALSE), (20, 'away', 4, TRUE)"""
    )
    result = check_results_scores(con)
    assert result.status == "FAIL"
    assert 20 in {row[0] for row in result.failing}
