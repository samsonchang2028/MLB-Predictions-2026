"""Tests for results.home_win_derivation regular-season scope (DATA-015).

The full 2021-2025 build surfaced 185 spring-training (``game_type='S'``) Final
games that this P0 check wrongly flagged: 184 legitimate TIES (equal scores, no
winner flag) plus one decisive spring game (game_pk 642061, 4-7) reported Final
with no ``is_winner`` set. ADR-004 scopes V1 to the REGULAR season, so the check
is restricted to ``game_type='R'``. Regular-season strictness must be preserved.
"""

from __future__ import annotations

import duckdb

from validation.checks import check_home_win_derivation


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA silver")
    con.execute(
        """CREATE TABLE silver.games (
               game_pk BIGINT, game_type VARCHAR, abstract_game_state VARCHAR
           )"""
    )
    con.execute(
        """CREATE TABLE silver.team_game_statistics (
               game_pk BIGINT, side VARCHAR, score INTEGER, is_winner BOOLEAN
           )"""
    )
    return con


def test_spring_training_tie_is_not_flagged() -> None:
    """(a) A spring-training tie (equal score, no winner) must not fail."""
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (100, 'S', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (100, 'home', 5, NULL), (100, 'away', 5, NULL)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "PASS", (result.message, result.failing)


def test_spring_training_decisive_game_without_winner_flag_is_not_flagged() -> None:
    """(b) A decisive spring game with no winner flag (like 642061) must not fail."""
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (642061, 'S', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (642061, 'home', 4, NULL), (642061, 'away', 7, NULL)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "PASS", (result.message, result.failing)


def test_regular_season_winner_disagrees_with_score_still_fails() -> None:
    """(c) Regular-season winner flag disagreeing with the score still fails (P0)."""
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (200, 'R', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (200, 'home', 3, TRUE), (200, 'away', 5, FALSE)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "FAIL"
    assert result.severity == "P0"
    assert 200 in {row for row in result.failing}


def test_regular_season_two_winners_still_fails() -> None:
    """(c) Regular-season game declaring two winners still fails (P0)."""
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (201, 'R', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (201, 'home', 5, TRUE), (201, 'away', 3, TRUE)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "FAIL"
    assert result.severity == "P0"
    assert 201 in {row for row in result.failing}


def test_regular_season_winner_on_equal_scores_still_fails() -> None:
    """(c) Regular-season game declaring a winner on equal scores still fails (P0)."""
    con = _connection()
    con.execute("INSERT INTO silver.games VALUES (202, 'R', 'Final')")
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (202, 'home', 4, TRUE), (202, 'away', 4, FALSE)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "FAIL"
    assert result.severity == "P0"
    assert 202 in {row for row in result.failing}


def test_regular_season_decisive_game_with_correct_winner_passes() -> None:
    """(d) A regular-season decisive game with the correct winner passes."""
    con = _connection()
    con.execute(
        """INSERT INTO silver.games VALUES
               (300, 'R', 'Final'),   -- home wins
               (301, 'R', 'Final')"""  # away wins
    )
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (300, 'home', 6, TRUE), (300, 'away', 2, FALSE),
               (301, 'home', 1, FALSE), (301, 'away', 8, TRUE)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "PASS", (result.message, result.failing)


def test_mixed_build_only_regular_season_inconsistency_is_flagged() -> None:
    """A mixed build: spring tie + spring decisive-no-flag pass; only the
    regular-season inconsistency is reported."""
    con = _connection()
    con.execute(
        """INSERT INTO silver.games VALUES
               (100, 'S', 'Final'),      -- spring tie
               (642061, 'S', 'Final'),   -- spring decisive, no winner flag
               (300, 'R', 'Final'),      -- regular, correct winner
               (200, 'R', 'Final')"""    # regular, winner disagrees
    )
    con.execute(
        """INSERT INTO silver.team_game_statistics VALUES
               (100, 'home', 5, NULL), (100, 'away', 5, NULL),
               (642061, 'home', 4, NULL), (642061, 'away', 7, NULL),
               (300, 'home', 6, TRUE), (300, 'away', 2, FALSE),
               (200, 'home', 3, TRUE), (200, 'away', 5, FALSE)"""
    )
    result = check_home_win_derivation(con)
    assert result.status == "FAIL"
    assert result.severity == "P0"
    assert set(result.failing) == {200}
