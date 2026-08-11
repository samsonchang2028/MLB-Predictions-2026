"""Unit tests for semantic-completeness / measure-column coverage (DATA-017).

These exercise the coverage checks against small hand-built Silver fixtures so
each documented threshold and hard-fail condition is covered directly. The core
regression is DATA-016: a required pitcher-stat column that is 100% NULL must
FAIL certification (naming the table/column), where the old structural-only
gate certified PASS.
"""

from __future__ import annotations

import duckdb

from validation.coverage import (
    DEGENERATE_MIN_SAMPLE,
    HOME_WIN_RATE_MIN_SAMPLE,
    coverage_report,
    run_coverage_checks,
)

# Every required pitcher stat column that must carry measurement content.
_PITCHER_STAT_COLUMNS = (
    "innings_pitched",
    "outs_recorded",
    "batters_faced",
    "earned_runs",
    "hits_allowed",
    "walks",
    "strikeouts",
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA silver")
    con.execute(
        """CREATE TABLE silver.games (
               game_pk BIGINT,
               game_type VARCHAR,
               abstract_game_state VARCHAR,
               game_date TIMESTAMP
           )"""
    )
    con.execute(
        """CREATE TABLE silver.team_game_statistics (
               game_pk BIGINT, team_id BIGINT, side VARCHAR,
               score INTEGER, is_winner BOOLEAN
           )"""
    )
    con.execute(
        """CREATE TABLE silver.pitcher_appearances (
               game_pk BIGINT, team_id BIGINT, side VARCHAR,
               pitcher_id BIGINT, appearance_order INTEGER,
               is_actual_starter BOOLEAN,
               innings_pitched VARCHAR, outs_recorded INTEGER,
               batters_faced INTEGER, pitches_thrown INTEGER,
               strikes INTEGER, balls INTEGER, hits_allowed INTEGER,
               runs_allowed INTEGER, earned_runs INTEGER,
               walks INTEGER, strikeouts INTEGER, home_runs_allowed INTEGER
           )"""
    )
    return con


def _add_game(
    con: duckdb.DuckDBPyConnection,
    game_pk: int,
    *,
    home_win: bool = True,
    game_type: str = "R",
    state: str = "Final",
) -> None:
    con.execute(
        "INSERT INTO silver.games VALUES (?, ?, ?, TIMESTAMP '2024-04-10 23:10:00')",
        [game_pk, game_type, state],
    )
    con.execute(
        "INSERT INTO silver.team_game_statistics VALUES (?, 111, 'home', ?, ?), (?, 222, 'away', ?, ?)",
        [game_pk, 5 if home_win else 2, home_win, game_pk, 2 if home_win else 5, not home_win],
    )


def _add_appearance(
    con: duckdb.DuckDBPyConnection,
    game_pk: int,
    team_id: int,
    side: str,
    pitcher_id: int,
    order: int,
    *,
    starter: bool,
    line: dict | None,
) -> None:
    """Insert an appearance. ``line=None`` inserts a fully-NULL stat line."""
    if line is None:
        line = {c: None for c in (
            "innings_pitched", "outs_recorded", "batters_faced", "pitches_thrown",
            "strikes", "balls", "hits_allowed", "runs_allowed", "earned_runs",
            "walks", "strikeouts", "home_runs_allowed",
        )}
    con.execute(
        """INSERT INTO silver.pitcher_appearances VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            game_pk, team_id, side, pitcher_id, order, starter,
            line["innings_pitched"], line["outs_recorded"], line["batters_faced"],
            line["pitches_thrown"], line["strikes"], line["balls"],
            line["hits_allowed"], line["runs_allowed"], line["earned_runs"],
            line["walks"], line["strikeouts"], line["home_runs_allowed"],
        ],
    )


def _full_line(pid: int) -> dict:
    return {
        "innings_pitched": "6.0",
        "outs_recorded": 18,
        "batters_faced": 24,
        "pitches_thrown": 90,
        "strikes": 60,
        "balls": 30,
        "hits_allowed": 5,
        "runs_allowed": 3 + (pid % 3),
        "earned_runs": 2 + (pid % 3),
        "walks": 1,
        "strikeouts": 7,
        "home_runs_allowed": 1,
    }


def _populated_dataset(con: duckdb.DuckDBPyConnection, n_games: int = 40) -> None:
    """A plausible regular-season dataset: each game has a starter + reliever per
    team, with a complete stat line, and a ~0.53 home-win rate."""
    for i in range(n_games):
        game_pk = 1000 + i
        _add_game(con, game_pk, home_win=(i % 2 == 0))  # ~0.5 home-win rate
        for team_id, side in ((111, "home"), (222, "away")):
            _add_appearance(con, game_pk, team_id, side, 900 + i, 1,
                            starter=True, line=_full_line(900 + i))
            _add_appearance(con, game_pk, team_id, side, 800 + i, 2,
                            starter=False, line=_full_line(800 + i))


def _by_check(results) -> dict:
    return {r.check: r for r in results}


# --------------------------------------------------------------------------- #
# Hard-fail: required stat column 100% NULL
# --------------------------------------------------------------------------- #
def test_all_null_required_stat_fails_naming_table_and_column() -> None:
    con = _connection()
    for i in range(5):
        game_pk = 100 + i
        _add_game(con, game_pk)
        # Starters present, but every pitching stat line is NULL (hollow build).
        _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=None)
        _add_appearance(con, game_pk, 222, "away", 700 + i, 1, starter=True, line=None)

    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "FAIL"
    assert result.severity == "P1"
    assert result.blocking is True
    # Names the table and a required column.
    assert "silver.pitcher_appearances.outs_recorded" in result.message
    assert "100% NULL" in result.message


def test_each_required_pitcher_stat_hard_fails_when_all_null() -> None:
    """Every declared required stat column, individually all-NULL, hard-fails."""
    for column in _PITCHER_STAT_COLUMNS:
        con = _connection()
        for i in range(4):
            game_pk = 100 + i
            _add_game(con, game_pk)
            line = _full_line(900 + i)
            line[column] = None  # knock out exactly one required column
            other = _full_line(700 + i)
            other[column] = None
            _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=line)
            _add_appearance(con, game_pk, 222, "away", 700 + i, 1, starter=True, line=other)
        result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
        assert result.status == "FAIL", column
        assert f"silver.pitcher_appearances.{column}" in result.message, column


# --------------------------------------------------------------------------- #
# Fully populated dataset passes
# --------------------------------------------------------------------------- #
def test_fully_populated_dataset_passes_all_semantic_checks() -> None:
    con = _connection()
    _populated_dataset(con, n_games=40)
    by_check = _by_check(run_coverage_checks(con))
    for name in (
        "semantic.pitcher_stat_coverage",
        "semantic.team_outcome_coverage",
        "semantic.feature_family_team",
        "semantic.feature_family_starter",
        "semantic.feature_family_bullpen",
        "semantic.feature_family_rest_schedule",
        "semantic.distribution_sanity",
    ):
        assert by_check[name].status == "PASS", (name, by_check[name].message)
    assert not any(r.blocking for r in by_check.values())


# --------------------------------------------------------------------------- #
# Partial / legitimate sparsity: WARN, never a hard fail
# --------------------------------------------------------------------------- #
def test_partial_sparsity_warns_and_does_not_hard_fail() -> None:
    con = _connection()
    _populated_dataset(con, n_games=40)
    # Null out one starter's strikeouts -> a small (<100%) sparsity, above the
    # 2% WARN floor but far from structurally empty.
    con.execute(
        "UPDATE silver.pitcher_appearances SET strikeouts = NULL WHERE game_pk < 1010"
    )
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "WARN"
    assert result.blocking is False
    assert "strikeouts" in result.message
    assert "null_rate" in result.message


def test_optional_pitches_thrown_all_null_does_not_fail() -> None:
    """pitches_thrown is documented optional (nullable older boxscores)."""
    con = _connection()
    _populated_dataset(con, n_games=40)
    con.execute("UPDATE silver.pitcher_appearances SET pitches_thrown = NULL")
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "PASS", result.message


def test_small_all_null_optional_only_still_passes_required() -> None:
    """A tiny fixture with complete required lines passes even below scale."""
    con = _connection()
    for i in range(2):
        game_pk = 100 + i
        _add_game(con, game_pk)
        _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=_full_line(900 + i))
        _add_appearance(con, game_pk, 222, "away", 700 + i, 1, starter=True, line=_full_line(700 + i))
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "PASS", result.message


# --------------------------------------------------------------------------- #
# Populated-but-broken: constant zero
# --------------------------------------------------------------------------- #
def test_constant_zero_required_stat_fails_at_scale() -> None:
    con = _connection()
    _populated_dataset(con, n_games=DEGENERATE_MIN_SAMPLE)  # >= min sample
    con.execute("UPDATE silver.pitcher_appearances SET outs_recorded = 0")
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "outs_recorded" in result.message
    assert "constant zero" in result.message


def test_constant_zero_below_scale_does_not_fail() -> None:
    """A small legitimately-zero sample (e.g. shutouts) must not fail."""
    con = _connection()
    _populated_dataset(con, n_games=3)  # far below DEGENERATE_MIN_SAMPLE
    con.execute("UPDATE silver.pitcher_appearances SET earned_runs = 0")
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "PASS", result.message


def test_implausible_outs_per_appearance_hard_fails() -> None:
    """A populated but corrupted stat line is not semantically complete."""
    con = _connection()
    _populated_dataset(con, n_games=3)
    con.execute("UPDATE silver.pitcher_appearances SET outs_recorded = 82")
    result = _by_check(run_coverage_checks(con))["semantic.pitcher_stat_coverage"]
    assert result.status == "FAIL"
    assert "outside plausible range" in result.message


# --------------------------------------------------------------------------- #
# Feature-family reports
# --------------------------------------------------------------------------- #
def test_starter_inputs_unusable_hard_fails() -> None:
    """Starters present but no complete stat line -> starter family unusable."""
    con = _connection()
    for i in range(5):
        game_pk = 100 + i
        _add_game(con, game_pk)
        _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=None)
        _add_appearance(con, game_pk, 222, "away", 700 + i, 1, starter=True, line=None)
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_starter"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "starter" in result.message


def test_bullpen_inputs_unusable_hard_fails() -> None:
    """Relievers present but outs_recorded all NULL -> bullpen family unusable."""
    con = _connection()
    for i in range(5):
        game_pk = 100 + i
        _add_game(con, game_pk)
        _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=_full_line(900 + i))
        # reliever with no usable workload proxy
        _add_appearance(con, game_pk, 111, "home", 500 + i, 2, starter=False, line=None)
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_bullpen"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "bullpen" in result.message


def test_bullpen_quality_inputs_unusable_hard_fails() -> None:
    """Workload alone cannot make the planned ERA/WHIP family usable."""
    con = _connection()
    for i in range(5):
        game_pk = 100 + i
        _add_game(con, game_pk)
        _add_appearance(
            con, game_pk, 111, "home", 900 + i, 1,
            starter=True, line=_full_line(900 + i),
        )
        relief = _full_line(500 + i)
        relief["earned_runs"] = None
        relief["hits_allowed"] = None
        relief["walks"] = None
        _add_appearance(
            con, game_pk, 111, "home", 500 + i, 2,
            starter=False, line=relief,
        )
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_bullpen"]
    assert result.status == "FAIL"
    assert "NONE are usable" in result.message


def test_empty_bullpen_family_below_scale_passes() -> None:
    """A tiny complete-game-only fixture legitimately has no relievers."""
    con = _connection()
    for i in range(3):  # below FAMILY_EMPTY_MIN_GAMES
        game_pk = 100 + i
        _add_game(con, game_pk)
        _add_appearance(con, game_pk, 111, "home", 900 + i, 1, starter=True, line=_full_line(900 + i))
        _add_appearance(con, game_pk, 222, "away", 700 + i, 1, starter=True, line=_full_line(700 + i))
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_bullpen"]
    assert result.status == "PASS", result.message


def test_empty_starter_family_at_scale_hard_fails() -> None:
    """Many regular-season games but no starter appearances at all -> empty."""
    con = _connection()
    for i in range(40):  # >= FAMILY_EMPTY_MIN_GAMES
        _add_game(con, 100 + i)
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_starter"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "entirely empty" in result.message


def test_team_family_reported_and_passes_when_populated() -> None:
    con = _connection()
    _populated_dataset(con, n_games=40)
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_team"]
    assert result.status == "PASS", result.message


def test_rest_schedule_family_reported() -> None:
    con = _connection()
    _populated_dataset(con, n_games=40)
    result = _by_check(run_coverage_checks(con))["semantic.feature_family_rest_schedule"]
    assert result.status == "PASS", result.message


# --------------------------------------------------------------------------- #
# Distribution sanity: home-win rate
# --------------------------------------------------------------------------- #
def test_home_win_rate_out_of_range_fails_at_scale() -> None:
    con = _connection()
    # Every game a home win -> home_win_rate = 1.0, well outside [0.45, 0.60].
    for i in range(HOME_WIN_RATE_MIN_SAMPLE + 10):
        game_pk = 2000 + i
        _add_game(con, game_pk, home_win=True)
        _add_appearance(con, game_pk, 111, "home", 900, 1, starter=True, line=_full_line(900))
        _add_appearance(con, game_pk, 222, "away", 700, 1, starter=True, line=_full_line(700))
    result = _by_check(run_coverage_checks(con))["semantic.distribution_sanity"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "home_win_rate" in result.message


def test_home_win_rate_out_of_range_below_scale_passes() -> None:
    con = _connection()
    for i in range(10):  # far below HOME_WIN_RATE_MIN_SAMPLE
        _add_game(con, 2000 + i, home_win=True)
    result = _by_check(run_coverage_checks(con))["semantic.distribution_sanity"]
    assert result.status == "PASS", result.message
    # Observed rate still recorded for drift visibility.
    assert "home_win_rate" in result.message


# --------------------------------------------------------------------------- #
# Team outcome coverage
# --------------------------------------------------------------------------- #
def test_all_null_score_fails() -> None:
    con = _connection()
    for i in range(5):
        game_pk = 100 + i
        con.execute(
            "INSERT INTO silver.games VALUES (?, 'R', 'Final', TIMESTAMP '2024-04-10 23:10:00')",
            [game_pk],
        )
        con.execute(
            "INSERT INTO silver.team_game_statistics VALUES (?, 111, 'home', NULL, TRUE), (?, 222, 'away', NULL, FALSE)",
            [game_pk, game_pk],
        )
    result = _by_check(run_coverage_checks(con))["semantic.team_outcome_coverage"]
    assert result.status == "FAIL"
    assert result.blocking is True
    assert "silver.team_game_statistics.score" in result.message


# --------------------------------------------------------------------------- #
# Coverage report shape (artifact numbers)
# --------------------------------------------------------------------------- #
def test_coverage_report_records_per_column_numbers() -> None:
    con = _connection()
    _populated_dataset(con, n_games=40)
    report = coverage_report(con)
    cols = report["columns"]
    stat = cols["silver.pitcher_appearances.outs_recorded"]
    assert stat["row_count"] == 160  # 40 games * 2 teams * 2 pitchers
    assert stat["non_null"] == 160
    assert stat["null_rate"] == 0.0
    assert stat["min"] == 18 and stat["max"] == 18
    assert stat["required"] is True
    assert stat["status"] == "PASS"
    assert stat["plausible_range"] == [0, 81]
    # Families and thresholds recorded for build-over-build comparison.
    assert set(report["families"]) >= {"team", "starter", "bullpen", "rest_schedule", "market"}
    starter = report["families"]["starter"]
    assert starter["row_count"] == starter["non_null_count"] > 0
    assert starter["null_rate"] == 0.0
    assert starter["status"] == "PASS"
    assert report["families"]["market"]["status"] == "EXCLUDED"
    assert report["thresholds"]["required_null_fail_rate"] == 1.0
    assert report["home_win_rate"]["home_win_rate"] is not None
    assert report["home_win_rate"]["status"] == "PASS"
    # Silver measurement columns outside current V1 feature inputs are declared
    # optional rather than silently inferred away.
    for column in ("strikes", "balls", "runs_allowed", "home_runs_allowed"):
        optional = cols[f"silver.pitcher_appearances.{column}"]
        assert optional["required"] is False
        assert optional["status"] == "PASS"
