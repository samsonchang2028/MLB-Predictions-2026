"""Semantic-completeness (measurement-content) checks for the Silver dataset.

Certification distinguishes three kinds of validity, all required for PASS
(ADR-005):

1. **structural**       - schema, keys, joins, cardinality, determinism
                          (``checks.py`` / ``leakage.py`` structural checks),
2. **semantic completeness** - required MEASUREMENT CONTENT is present and
                          plausible (this module),
3. **temporal/leakage** - point-in-time correctness (``leakage.py``).

Motivation (ADR-005 / DATA-016): the 2021-2025 build certified PASS while every
pitching stat in ``silver.pitcher_appearances`` was 100% NULL across 132,848
rows. Structure, identity, joins, determinism, and temporal behaviour were all
valid; nothing asserted that the substantive measure columns actually contained
values. These checks close that gap.

This is intentionally MINIMAL and BASEBALL-SPECIFIC — an explicit allowlist of
the measure columns and feature families V1 actually consumes — not a generic
data-quality framework.

Declared measure columns (allowlist)
------------------------------------
``silver.pitcher_appearances`` (evaluated over regular-season appearances,
``games.game_type = 'R'`` — the scope every FEAT family consumes):

- required stat line: ``innings_pitched``, ``outs_recorded``, ``batters_faced``,
  ``earned_runs``, ``hits_allowed``, ``walks``, ``strikeouts``. Every pitcher who
  appears in a completed MLB game has these recorded in the boxscore, so a
  100%-NULL required column is the DATA-016 hollow-build defect and is
  merge-blocking (P1).
- optional: ``pitches_thrown`` — explicitly documented as legitimately absent in
  older boxscores (see ``starter.py`` / ``bullpen.py``); best-effort workload
  proxy, so it is reported but never hard-fails.
- optional/not consumed by a declared V1 feature: ``strikes``, ``balls``,
  ``runs_allowed``, and ``home_runs_allowed``. They remain explicitly declared
  and reported so Silver measurements cannot disappear from certification by
  inference, but their absence does not block the V1 feature families.

``silver.team_game_statistics`` (evaluated over regular-season Final games,
``game_type = 'R' AND abstract_game_state = 'Final'`` — the only population where
these outcomes must exist; postponed/preview games legitimately lack them):

- required: ``score``, ``is_winner``.

Required feature families (fail when a required family's inputs are unusable):

- **team**    - regular-season Final ``team_game_statistics`` rows with both
                ``score`` and ``is_winner`` populated,
- **starter** - regular-season actual-starter appearances carrying a complete
                required stat line (matches ``starter.py`` ``_LINE_REQUIRED``),
- **bullpen** - regular-season non-starter appearances (``appearance_order > 1``)
                with ``outs_recorded``, ``earned_runs``, ``hits_allowed``, and
                ``walks`` populated for workload, ERA, and WHIP inputs,
- **rest/schedule** - regular-season games with a usable ``game_date`` timestamp.

Market/odds features are intentionally EXCLUDED from hard-fail: ADR-004 states
incomplete historical odds coverage must not block core baseball development.

Documented thresholds (every threshold has a reason)
----------------------------------------------------
- ``REQUIRED_NULL_FAIL_RATE = 1.0`` — a required measure column that is 100% NULL
  hard-fails (P1). Reason: a completed-game boxscore always records these; a fully
  empty required column can only be a broken ingestion projection (DATA-016), not
  legitimate sparsity. Below 100% but above the WARN floor is reported as WARN,
  never silently dropped.
- ``COVERAGE_WARN_RATE = 0.02`` — >2% missing on a required column is surfaced as
  a WARN with the observed rate. Reason: a small residue of genuinely missing
  lines (data corrections, suspended games) is tolerable, but a rising null rate
  is an early signal of a partial-ingestion regression and must stay visible
  build-over-build. It is advisory, not blocking, because some missingness is
  legitimate.
- ``DEGENERATE_MIN_SAMPLE = 100`` — constant-zero degeneracy (a required numeric
  stat whose values are all zero) only hard-fails once at least this many
  observations exist. Reason: across the historical dataset the SUM of outs,
  batters faced, hits, walks, strikeouts, or earned runs cannot be zero; but a
  tiny fixture of, say, two shutout starts is legitimately all-zero for
  ``earned_runs``, so the check targets a broken pipeline emitting a constant
  across the whole dataset, not small legitimate samples.
- ``FAMILY_EMPTY_MIN_GAMES = 30`` — a required family with ZERO declared inputs
  hard-fails only once the dataset spans at least this many regular-season games.
  Reason: a full historical build has thousands of games where relievers and
  starters always appear, so zero inputs means the family was never ingested; but
  a handful of complete-game fixtures can legitimately have no bullpen
  appearances. A family that HAS inputs but none are usable (the hollow-build
  case) hard-fails regardless of sample size.
- ``HOME_WIN_RATE_RANGE = (0.45, 0.60)`` with ``HOME_WIN_RATE_MIN_SAMPLE = 300`` —
  the leaguewide home-win rate is ~0.53-0.54 and only converges over a
  season-scale sample, so the plausible-range hard-fail is evaluated only at >=300
  regular-season Final games. Reason: on a small fixture the observed rate is
  noisy and would false-fail; at scale a rate outside [0.45, 0.60] indicates a
  broken ``is_winner`` / ``score`` derivation. The observed rate is always
  recorded so drift is visible.
- ``MAX_OUTS_PER_APPEARANCE = 81`` and ``MAX_EARNED_RUNS_PER_APPEARANCE = 50`` —
  conservative hard bounds for one pitcher's line in one game. They are well
  above any plausible modern MLB appearance (81 outs is 27 innings), so these
  reject unit/projection corruption without encoding normal performance.

All checks are deterministic and side-effect free (read-only SQL aggregates),
consistent with the existing certification checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from validation.results import CheckResult, fail, ok, warn

# --------------------------------------------------------------------------- #
# Documented thresholds (reasons in the module docstring)
# --------------------------------------------------------------------------- #
REQUIRED_NULL_FAIL_RATE = 1.0
COVERAGE_WARN_RATE = 0.02
DEGENERATE_MIN_SAMPLE = 100
FAMILY_EMPTY_MIN_GAMES = 30
HOME_WIN_RATE_RANGE = (0.45, 0.60)
HOME_WIN_RATE_MIN_SAMPLE = 300
MAX_OUTS_PER_APPEARANCE = 81
MAX_EARNED_RUNS_PER_APPEARANCE = 50


@dataclass(frozen=True)
class MeasureColumn:
    """A declared Silver measure column and how it must be populated."""

    table: str
    column: str
    required: bool
    numeric: bool
    zero_is_degenerate: bool = False
    plausible_min: int | float | None = None
    plausible_max: int | float | None = None

    @property
    def qualified(self) -> str:
        return f"silver.{self.table}.{self.column}"


# Regular-season pitcher stat line (allowlist). ``game_type = 'R'`` scope.
PITCHER_APPEARANCE_MEASURES: tuple[MeasureColumn, ...] = (
    MeasureColumn("pitcher_appearances", "innings_pitched", required=True, numeric=False),
    MeasureColumn(
        "pitcher_appearances",
        "outs_recorded",
        required=True,
        numeric=True,
        zero_is_degenerate=True,
        plausible_min=0,
        plausible_max=MAX_OUTS_PER_APPEARANCE,
    ),
    MeasureColumn("pitcher_appearances", "batters_faced", required=True, numeric=True, zero_is_degenerate=True),
    MeasureColumn(
        "pitcher_appearances",
        "earned_runs",
        required=True,
        numeric=True,
        zero_is_degenerate=True,
        plausible_min=0,
        plausible_max=MAX_EARNED_RUNS_PER_APPEARANCE,
    ),
    MeasureColumn("pitcher_appearances", "hits_allowed", required=True, numeric=True, zero_is_degenerate=True),
    MeasureColumn("pitcher_appearances", "walks", required=True, numeric=True, zero_is_degenerate=True),
    MeasureColumn("pitcher_appearances", "strikeouts", required=True, numeric=True, zero_is_degenerate=True),
    # Documented optional: nullable in older boxscores (starter.py/bullpen.py).
    MeasureColumn("pitcher_appearances", "pitches_thrown", required=False, numeric=True),
    # Explicitly outside declared V1 feature inputs; retained for drift reporting.
    MeasureColumn("pitcher_appearances", "strikes", required=False, numeric=True),
    MeasureColumn("pitcher_appearances", "balls", required=False, numeric=True),
    MeasureColumn("pitcher_appearances", "runs_allowed", required=False, numeric=True),
    MeasureColumn(
        "pitcher_appearances", "home_runs_allowed", required=False, numeric=True
    ),
)

# Team outcome measures (allowlist). Regular-season Final scope.
TEAM_OUTCOME_MEASURES: tuple[MeasureColumn, ...] = (
    MeasureColumn("team_game_statistics", "score", required=True, numeric=True, zero_is_degenerate=True),
    MeasureColumn("team_game_statistics", "is_winner", required=True, numeric=False),
)

# WHERE clauses defining the population each measure family is required over.
_REGULAR_APPEARANCES = (
    "FROM silver.pitcher_appearances a JOIN silver.games g USING (game_pk) "
    "WHERE g.game_type = 'R'"
)
_REGULAR_FINAL_TEAM = (
    "FROM silver.team_game_statistics t JOIN silver.games g USING (game_pk) "
    "WHERE g.game_type = 'R' AND g.abstract_game_state = 'Final'"
)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def run_coverage_checks(connection: Any) -> list[CheckResult]:
    """Run every semantic-completeness check in a stable order.

    Returns ``CheckResult``s with ``semantic.`` check ids so the certification
    layer can report semantic completeness as its own validity dimension. If the
    Silver dataset is absent, returns nothing (``data.silver_present`` already
    FAILs in that case).
    """
    if not _table_exists(connection, "silver", "games"):
        return []
    report = coverage_report(connection)
    return [
        _pitcher_stat_check(report),
        _team_outcome_check(report),
        _family_check("team", report),
        _family_check("starter", report),
        _family_check("bullpen", report),
        _family_check("rest_schedule", report),
        _distribution_sanity_check(report),
    ]


def coverage_report(connection: Any) -> dict[str, Any]:
    """Deterministic per-column and per-family coverage numbers for the artifact.

    Recording these makes builds comparable over time (drift is visible even when
    the build still PASSes). Pure over the committed Silver dataset.
    """
    regular_games = _scalar(
        connection,
        "SELECT count(*) FROM silver.games WHERE game_type = 'R'",
    )
    columns: dict[str, Any] = {}
    for measure in (*PITCHER_APPEARANCE_MEASURES, *TEAM_OUTCOME_MEASURES):
        source = (
            _REGULAR_APPEARANCES
            if measure.table == "pitcher_appearances"
            else _REGULAR_FINAL_TEAM
        )
        alias = "a" if measure.table == "pitcher_appearances" else "t"
        columns[measure.qualified] = _column_stats(connection, measure, source, alias)

    return {
        "regular_season_games": regular_games,
        "thresholds": {
            "required_null_fail_rate": REQUIRED_NULL_FAIL_RATE,
            "coverage_warn_rate": COVERAGE_WARN_RATE,
            "degenerate_min_sample": DEGENERATE_MIN_SAMPLE,
            "family_empty_min_games": FAMILY_EMPTY_MIN_GAMES,
            "home_win_rate_range": list(HOME_WIN_RATE_RANGE),
            "home_win_rate_min_sample": HOME_WIN_RATE_MIN_SAMPLE,
            "max_outs_per_appearance": MAX_OUTS_PER_APPEARANCE,
            "max_earned_runs_per_appearance": MAX_EARNED_RUNS_PER_APPEARANCE,
        },
        "columns": columns,
        "families": _family_stats(connection, regular_games),
        "home_win_rate": _home_win_rate_stats(connection),
    }


# --------------------------------------------------------------------------- #
# Column coverage
# --------------------------------------------------------------------------- #
def _column_stats(
    connection: Any,
    measure: MeasureColumn,
    source: str,
    alias: str,
) -> dict[str, Any]:
    col = f"{alias}.{measure.column}"
    if measure.numeric:
        row = _fetch(
            connection,
            f"SELECT count(*), count({col}), min({col}), max({col}) {source}",
        )[0]
        row_count, non_null, col_min, col_max = row
    else:
        row = _fetch(connection, f"SELECT count(*), count({col}) {source}")[0]
        row_count, non_null = row
        col_min = col_max = None
    null_rate = None if row_count == 0 else (row_count - non_null) / row_count
    all_zero = bool(
        measure.zero_is_degenerate
        and non_null > 0
        and col_min == 0
        and col_max == 0
    )
    stats = {
        "table": f"silver.{measure.table}",
        "column": measure.column,
        "required": measure.required,
        "row_count": row_count,
        "non_null": non_null,
        "null_rate": null_rate,
        "min": col_min,
        "max": col_max,
        "all_zero": all_zero,
        "plausible_range": [measure.plausible_min, measure.plausible_max],
    }
    stats["status"] = _column_status(measure, stats)
    return stats


def _column_status(measure: MeasureColumn, stats: dict[str, Any]) -> str:
    """Return the declared field's deterministic PASS/WARN/FAIL status."""
    if not measure.required or stats["row_count"] == 0:
        return "PASS"
    if stats["null_rate"] >= REQUIRED_NULL_FAIL_RATE:
        return "FAIL"
    if stats["all_zero"] and stats["non_null"] >= DEGENERATE_MIN_SAMPLE:
        return "FAIL"
    below_min = (
        measure.plausible_min is not None
        and stats["min"] is not None
        and stats["min"] < measure.plausible_min
    )
    above_max = (
        measure.plausible_max is not None
        and stats["max"] is not None
        and stats["max"] > measure.plausible_max
    )
    if below_min or above_max:
        return "FAIL"
    if stats["null_rate"] > COVERAGE_WARN_RATE:
        return "WARN"
    return "PASS"


def _pitcher_stat_check(report: dict[str, Any]) -> CheckResult:
    return _measure_group_check(
        "semantic.pitcher_stat_coverage",
        report,
        PITCHER_APPEARANCE_MEASURES,
    )


def _team_outcome_check(report: dict[str, Any]) -> CheckResult:
    return _measure_group_check(
        "semantic.team_outcome_coverage",
        report,
        TEAM_OUTCOME_MEASURES,
    )


def _measure_group_check(
    check: str, report: dict[str, Any], measures: tuple[MeasureColumn, ...]
) -> CheckResult:
    """Aggregate a group of declared measure columns into one status.

    FAIL (P1, merge-blocking) when a required column is 100% NULL or is a
    degenerate all-zero constant at scale; WARN when a required column is below
    the coverage floor; else PASS.
    """
    columns = report["columns"]
    failures: list[str] = []
    warnings: list[str] = []

    for measure in measures:
        stats = columns[measure.qualified]
        if stats["row_count"] == 0:
            continue  # empty population is a family-level concern, not a column one
        null_rate = stats["null_rate"]
        if measure.required and null_rate is not None and null_rate >= REQUIRED_NULL_FAIL_RATE:
            failures.append(
                f"{measure.qualified} is 100% NULL "
                f"(0/{stats['row_count']} populated)"
            )
            continue
        if (
            measure.required
            and stats["all_zero"]
            and stats["non_null"] >= DEGENERATE_MIN_SAMPLE
        ):
            failures.append(
                f"{measure.qualified} is a constant zero across all "
                f"{stats['non_null']} populated rows (degenerate/broken)"
            )
            continue
        if measure.required and stats["status"] == "FAIL":
            failures.append(
                f"{measure.qualified} observed range "
                f"[{stats['min']}, {stats['max']}] is outside plausible range "
                f"{stats['plausible_range']}"
            )
            continue
        if measure.required and null_rate is not None and null_rate > COVERAGE_WARN_RATE:
            warnings.append(
                f"{measure.qualified} null_rate={null_rate:.3f} "
                f"({stats['non_null']}/{stats['row_count']} populated)"
            )

    if failures:
        return fail(check, "P1", "; ".join(failures), failures)
    if warnings:
        return warn(check, "; ".join(warnings), warnings)
    return ok(check, "P1")


# --------------------------------------------------------------------------- #
# Feature-family reports
# --------------------------------------------------------------------------- #
def _family_stats(connection: Any, regular_games: int) -> dict[str, Any]:
    starter_line = " AND ".join(
        f"a.{field} IS NOT NULL"
        for field in (
            "outs_recorded",
            "earned_runs",
            "hits_allowed",
            "walks",
            "strikeouts",
            "batters_faced",
        )
    )
    team_required = _scalar(connection, f"SELECT count(*) {_REGULAR_FINAL_TEAM}")
    team_usable = _scalar(
        connection,
        f"SELECT count(*) {_REGULAR_FINAL_TEAM} "
        "AND t.score IS NOT NULL AND t.is_winner IS NOT NULL",
    )
    starter_total = _scalar(
        connection,
        f"SELECT count(*) {_REGULAR_APPEARANCES} AND a.is_actual_starter",
    )
    starter_usable = _scalar(
        connection,
        f"SELECT count(*) {_REGULAR_APPEARANCES} AND a.is_actual_starter "
        f"AND {starter_line}",
    )
    bullpen_total = _scalar(
        connection,
        f"SELECT count(*) {_REGULAR_APPEARANCES} AND a.appearance_order > 1",
    )
    bullpen_usable = _scalar(
        connection,
        f"SELECT count(*) {_REGULAR_APPEARANCES} AND a.appearance_order > 1 "
        "AND a.outs_recorded IS NOT NULL AND a.earned_runs IS NOT NULL "
        "AND a.hits_allowed IS NOT NULL AND a.walks IS NOT NULL",
    )
    schedule_usable = _scalar(
        connection,
        "SELECT count(*) FROM silver.games "
        "WHERE game_type = 'R' AND game_date IS NOT NULL",
    )
    families = {
        "team": {
            "required": "team_game_statistics(score, is_winner) over regular-season Final games",
            "declared": team_required,
            "usable": team_usable,
        },
        "starter": {
            "required": "actual-starter appearances with a complete stat line",
            "declared": starter_total,
            "usable": starter_usable,
        },
        "bullpen": {
            "required": "relief appearances with workload/ERA/WHIP inputs",
            "declared": bullpen_total,
            "usable": bullpen_usable,
        },
        "rest_schedule": {
            "required": "regular-season games with a usable game_date",
            "declared": regular_games,
            "usable": schedule_usable,
        },
        "market": {
            "required": "odds coverage — intentionally NOT merge-blocking (ADR-004)",
            "declared": None,
            "usable": None,
            "status": "EXCLUDED",
        },
    }
    for name, family in families.items():
        if name == "market":
            continue
        declared = family["declared"]
        usable = family["usable"]
        family["row_count"] = declared
        family["non_null_count"] = usable
        family["null_rate"] = (
            None if not declared else (declared - usable) / declared
        )
        # A family is a population predicate, not a scalar measure; min/max are
        # intentionally not meaningful but remain explicit in the artifact.
        family["min"] = None
        family["max"] = None
        family["status"] = _family_status(declared, usable, regular_games)
    return families


def _family_status(declared: int, usable: int, regular_games: int) -> str:
    if declared and usable == 0:
        return "FAIL"
    if not declared and regular_games >= FAMILY_EMPTY_MIN_GAMES:
        return "FAIL"
    if declared and (declared - usable) / declared > COVERAGE_WARN_RATE:
        return "WARN"
    return "PASS"


def _family_check(name: str, report: dict[str, Any]) -> CheckResult:
    """Fail when a required feature family's inputs are unusable.

    - inputs present but NONE usable (the hollow-build starter/bullpen case) ->
      FAIL regardless of sample size,
    - inputs entirely absent -> FAIL only at ``FAMILY_EMPTY_MIN_GAMES`` scale
      (a small complete-game fixture may legitimately lack relievers),
    - otherwise PASS with the observed usable/declared counts.
    """
    check = f"semantic.feature_family_{name}"
    family = report["families"][name]
    declared = family["declared"]
    usable = family["usable"]
    games = report["regular_season_games"]
    detail = [f"{name}: {usable}/{declared} usable inputs"]

    if declared and usable == 0:
        return fail(
            check,
            "P1",
            f"required feature family '{name}' has inputs but NONE are usable "
            f"({family['required']})",
            detail,
        )
    if not declared and games >= FAMILY_EMPTY_MIN_GAMES:
        return fail(
            check,
            "P1",
            f"required feature family '{name}' is entirely empty across "
            f"{games} regular-season games ({family['required']})",
            detail,
        )
    if family["status"] == "WARN":
        return warn(
            check,
            f"required feature family '{name}' has "
            f"null_rate={family['null_rate']:.3f} "
            f"({usable}/{declared} usable inputs)",
            detail,
        )
    return ok(check, "P1")


# --------------------------------------------------------------------------- #
# Distribution sanity
# --------------------------------------------------------------------------- #
def _home_win_rate_stats(connection: Any) -> dict[str, Any]:
    row = _fetch(
        connection,
        """SELECT count(*),
                  count(*) FILTER (WHERE t.is_winner)
           FROM silver.team_game_statistics t
           JOIN silver.games g USING (game_pk)
           WHERE g.game_type = 'R' AND g.abstract_game_state = 'Final'
             AND t.side = 'home' AND t.is_winner IS NOT NULL""",
    )[0]
    games, home_wins = row
    rate = None if games == 0 else home_wins / games
    lo, hi = HOME_WIN_RATE_RANGE
    status = (
        "FAIL"
        if games >= HOME_WIN_RATE_MIN_SAMPLE
        and rate is not None
        and not (lo <= rate <= hi)
        else "PASS"
    )
    return {
        "games": games,
        "home_wins": home_wins,
        "home_win_rate": rate,
        "plausible_range": list(HOME_WIN_RATE_RANGE),
        "status": status,
    }


def _distribution_sanity_check(report: dict[str, Any]) -> CheckResult:
    """Populated-but-broken detection: home-win rate outside its plausible range.

    Only evaluated at ``HOME_WIN_RATE_MIN_SAMPLE`` regular-season Final games so a
    noisy small sample cannot false-fail; the observed rate is always recorded.
    """
    check = "semantic.distribution_sanity"
    stats = report["home_win_rate"]
    games, rate = stats["games"], stats["home_win_rate"]
    lo, hi = HOME_WIN_RATE_RANGE
    if rate is None:
        return ok(check, "P1")
    observed = f"home_win_rate={rate:.4f} over {games} regular-season Final games"
    if games >= HOME_WIN_RATE_MIN_SAMPLE and not (lo <= rate <= hi):
        return fail(
            check,
            "P1",
            f"{observed} is outside the plausible range [{lo}, {hi}] "
            "(broken is_winner/score derivation)",
            [observed],
        )
    return ok(check, "P1", observed)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _table_exists(connection: Any, schema: str, name: str) -> bool:
    return (
        _scalar(
            connection,
            """SELECT count(*) FROM information_schema.tables
               WHERE table_schema = ? AND table_name = ?""",
            [schema, name],
        )
        > 0
    )


def _scalar(connection: Any, sql: str, params: list[object] | None = None) -> Any:
    return connection.execute(sql, params or []).fetchone()[0]


def _fetch(connection: Any, sql: str, params: list[object] | None = None) -> list[tuple]:
    return connection.execute(sql, params or []).fetchall()
