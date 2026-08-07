"""Deterministic Bronze/Silver validation checks over a DuckDB connection.

Each check is a small pure-ish function returning one ``CheckResult``. Checks
read committed Bronze/Silver data (and, where given, the raw payload tree) and
never mutate it, except ``check_processing_determinism`` which deterministically
rebuilds Silver to prove reproducibility.

Severity policy (ADR-004): P0/P1 findings are merge-blocking; P2 and WARN are
advisory. Constraint-backed invariants (e.g. home != away) are still validated
here so certification does not rely solely on the storage engine having enforced
them at load time.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from validation.results import CheckResult, fail, finding, ok, warn

_INNINGS_RE = re.compile(r"^\d+\.[012]$")
_KNOWN_ABSTRACT_STATES = ("Preview", "Live", "Final")
_PITCHING_INT_COLUMNS = (
    "outs_recorded",
    "batters_faced",
    "pitches_thrown",
    "strikes",
    "balls",
    "hits_allowed",
    "runs_allowed",
    "earned_runs",
    "walks",
    "strikeouts",
    "home_runs_allowed",
)


def run_data_checks(
    connection: Any, storage_root: str | Path | None = None
) -> list[CheckResult]:
    """Run every Bronze/Silver check and return their results in a stable order."""
    if not _table_exists(connection, "silver", "games"):
        return [fail("data.silver_present", "P1", "silver.games table is missing")]

    results = [
        check_silver_present(connection),
        check_game_pk_unique(connection),
        check_home_away_distinct(connection),
        check_valid_team_ids(connection),
        check_game_status_known(connection),
        check_ref_team_statistics(connection),
        check_ref_pitcher_appearances(connection),
        check_ref_pitcher_starters(connection),
        check_results_scores(connection),
        check_home_win_derivation(connection),
        check_status_result_consistency(connection),
        check_appearance_order_contiguous(connection),
        check_starter_appearance_agreement(connection),
        check_innings_domain(connection),
        check_doubleheader_integrity(connection),
        check_non_regular_season_pitchers(connection),
        check_bronze_detail_payloads(connection, storage_root),
        check_bronze_schedule_raw(connection, storage_root),
        check_bronze_provenance(connection),
        check_processing_determinism(connection),
    ]
    return results


# --------------------------------------------------------------------------- #
# Game identity
# --------------------------------------------------------------------------- #
def check_silver_present(connection: Any) -> CheckResult:
    count = _scalar(connection, "SELECT count(*) FROM silver.games")
    if count == 0:
        return fail("data.silver_present", "P1", "silver.games is empty")
    return ok("data.silver_present", "P1", f"{count} games")


def check_game_pk_unique(connection: Any) -> CheckResult:
    bad = _fetch(
        connection,
        """SELECT game_pk FROM silver.games
           GROUP BY game_pk HAVING count(*) > 1 ORDER BY game_pk""",
    )
    return finding("identity.game_pk_unique", "P0", _flatten(bad), "duplicate game_pk")


def check_home_away_distinct(connection: Any) -> CheckResult:
    bad = _fetch(
        connection,
        "SELECT game_pk FROM silver.games WHERE home_team_id = away_team_id ORDER BY game_pk",
    )
    return finding(
        "identity.home_away_distinct", "P0", _flatten(bad), "games with home == away team"
    )


def check_valid_team_ids(connection: Any) -> CheckResult:
    bad = _fetch(
        connection,
        """SELECT game_pk FROM silver.games
           WHERE home_team_id IS NULL OR away_team_id IS NULL
              OR home_team_id <= 0 OR away_team_id <= 0
           ORDER BY game_pk""",
    )
    return finding("identity.valid_team_ids", "P1", _flatten(bad), "games with invalid team ids")


def check_game_status_known(connection: Any) -> CheckResult:
    placeholders = ", ".join("?" for _ in _KNOWN_ABSTRACT_STATES)
    bad = _fetch(
        connection,
        f"""SELECT game_pk, abstract_game_state FROM silver.games
            WHERE abstract_game_state IS NULL
               OR abstract_game_state NOT IN ({placeholders})
            ORDER BY game_pk""",
        list(_KNOWN_ABSTRACT_STATES),
    )
    if bad:
        return warn(
            "identity.game_status_known",
            f"{len(bad)} games with unrecognized abstract_game_state",
            [tuple(row) for row in bad],
        )
    return ok("identity.game_status_known")


# --------------------------------------------------------------------------- #
# Referential integrity
# --------------------------------------------------------------------------- #
def check_ref_team_statistics(connection: Any) -> CheckResult:
    bad = _orphan_side_rows(connection, "team_game_statistics")
    return finding(
        "ref.team_statistics_game", "P0", bad, "team-game rows with no matching game/team"
    )


def check_ref_pitcher_appearances(connection: Any) -> CheckResult:
    bad = _orphan_side_rows(connection, "pitcher_appearances")
    return finding(
        "ref.pitcher_appearance_game", "P0", bad, "pitcher appearances with no matching game/team"
    )


def check_ref_pitcher_starters(connection: Any) -> CheckResult:
    bad = _orphan_side_rows(connection, "pitcher_starters")
    return finding(
        "ref.pitcher_starter_game", "P0", bad, "pitcher starters with no matching game/team"
    )


def _orphan_side_rows(connection: Any, table: str) -> list[tuple[object, ...]]:
    """Rows whose (game_pk, side, team_id) do not resolve to a silver.games team."""
    return [
        tuple(row)
        for row in _fetch(
            connection,
            f"""
            SELECT c.game_pk, c.team_id, c.side
            FROM silver.{table} AS c
            LEFT JOIN silver.games AS g
              ON g.game_pk = c.game_pk
             AND ((c.side = 'home' AND c.team_id = g.home_team_id)
               OR (c.side = 'away' AND c.team_id = g.away_team_id))
            WHERE g.game_pk IS NULL
            ORDER BY c.game_pk, c.side
            """,
        )
    ]


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
def check_results_scores(connection: Any) -> CheckResult:
    """Final games must carry a non-negative score for each team side."""
    bad = _fetch(
        connection,
        """SELECT g.game_pk, t.side, t.score
           FROM silver.games g
           JOIN silver.team_game_statistics t USING (game_pk)
           WHERE g.abstract_game_state = 'Final'
             AND (t.score IS NULL OR t.score < 0)
           ORDER BY g.game_pk, t.side""",
    )
    return finding(
        "results.valid_scores", "P1", [tuple(r) for r in bad], "Final team rows with missing/negative score"
    )


def check_home_win_derivation(connection: Any) -> CheckResult:
    """For Final games, is_winner must equal (that side scored more), with
    exactly one winner and unequal scores."""
    rows = _fetch(
        connection,
        """SELECT g.game_pk, t.side, t.score, t.is_winner
           FROM silver.games g
           JOIN silver.team_game_statistics t USING (game_pk)
           WHERE g.abstract_game_state = 'Final'
           ORDER BY g.game_pk, t.side""",
    )
    sides: dict[Any, dict[str, tuple[Any, Any]]] = {}
    for game_pk, side, score, is_winner in rows:
        sides.setdefault(game_pk, {})[side] = (score, is_winner)

    bad: list[object] = []
    for game_pk, by_side in sides.items():
        home, away = by_side.get("home"), by_side.get("away")
        if home is None or away is None:
            continue  # unpaired Final rows are covered by results.valid_scores
        (hs, hw), (as_, aw) = home, away
        if hs is None or as_ is None:
            continue
        if hs == as_ or hw == aw or (hs > as_) != bool(hw) or (as_ > hs) != bool(aw):
            bad.append(game_pk)
    return finding(
        "results.home_win_derivation", "P0", sorted(bad, key=repr), "Final games with inconsistent home_win"
    )


def check_status_result_consistency(connection: Any) -> CheckResult:
    """Postponed/cancelled/suspended (non-Final) games must not declare a winner."""
    bad = _fetch(
        connection,
        """SELECT g.game_pk, g.detailed_state
           FROM silver.games g
           JOIN silver.team_game_statistics t USING (game_pk)
           WHERE g.abstract_game_state <> 'Final' AND t.is_winner = TRUE
           ORDER BY g.game_pk""",
    )
    return finding(
        "results.status_consistency", "P1", [tuple(r) for r in bad], "non-Final games with a declared winner"
    )


# --------------------------------------------------------------------------- #
# Pitching
# --------------------------------------------------------------------------- #
def check_appearance_order_contiguous(connection: Any) -> CheckResult:
    """appearance_order per (game_pk, team_id) must be 1..N with exactly one starter."""
    rows = _fetch(
        connection,
        """SELECT game_pk, team_id,
                  min(appearance_order), max(appearance_order), count(*),
                  count(*) FILTER (WHERE is_actual_starter)
           FROM silver.pitcher_appearances
           GROUP BY game_pk, team_id
           ORDER BY game_pk, team_id""",
    )
    bad = [
        (game_pk, team_id)
        for game_pk, team_id, lo, hi, n, starters in rows
        if lo != 1 or hi != n or starters != 1
    ]
    return finding(
        "pitching.appearance_order", "P1", bad, "teams with non-contiguous order or wrong starter count"
    )


def check_starter_appearance_agreement(connection: Any) -> CheckResult:
    """The order-1 appearance pitcher must equal the recorded actual starter."""
    bad = _fetch(
        connection,
        """SELECT s.game_pk, s.team_id
           FROM silver.pitcher_starters s
           JOIN silver.pitcher_appearances a
             ON a.game_pk = s.game_pk AND a.team_id = s.team_id AND a.appearance_order = 1
           WHERE s.actual_pitcher_id IS NOT NULL
             AND s.actual_pitcher_id <> a.pitcher_id
           ORDER BY s.game_pk, s.team_id""",
    )
    return finding(
        "pitching.starter_agreement", "P1", [tuple(r) for r in bad], "starter id disagrees with order-1 appearance"
    )


def check_innings_domain(connection: Any) -> CheckResult:
    """innings_pitched must look like N.[012]; integer pitching stats non-negative."""
    columns = ", ".join(_PITCHING_INT_COLUMNS)
    rows = _fetch(
        connection,
        f"""SELECT game_pk, team_id, pitcher_id, innings_pitched, {columns}
            FROM silver.pitcher_appearances
            ORDER BY game_pk, team_id, appearance_order""",
    )
    bad: list[object] = []
    for row in rows:
        game_pk, team_id, pitcher_id, innings = row[0], row[1], row[2], row[3]
        ints = row[4:]
        if innings is not None and _INNINGS_RE.match(innings) is None:
            bad.append((game_pk, team_id, pitcher_id, "innings", innings))
            continue
        negatives = [v for v in ints if isinstance(v, int) and v < 0]
        if negatives:
            bad.append((game_pk, team_id, pitcher_id, "negative_stat"))
    return finding(
        "pitching.stat_domains", "P1", bad, "appearances with invalid innings/stat domain"
    )


def check_non_regular_season_pitchers(connection: Any) -> CheckResult:
    """Flag pitcher appearances tied to non-regular-season games (S/E/etc.).

    DATA-005 P3(c): spring-training/exhibition games are fetched and produce
    appearances. This is advisory so downstream FEAT tasks can exclude them.
    """
    bad = _fetch(
        connection,
        """SELECT DISTINCT a.game_pk, g.game_type
           FROM silver.pitcher_appearances a
           JOIN silver.games g USING (game_pk)
           WHERE g.game_type IS DISTINCT FROM 'R'
           ORDER BY a.game_pk""",
    )
    if bad:
        return warn(
            "pitching.non_regular_season",
            f"{len(bad)} non-regular-season games have pitcher appearances",
            [tuple(r) for r in bad],
        )
    return ok("pitching.non_regular_season")


# --------------------------------------------------------------------------- #
# Doubleheaders / same-day same-team
# --------------------------------------------------------------------------- #
def check_doubleheader_integrity(connection: Any) -> CheckResult:
    """Same-day games between the same teams must keep distinct game_number.

    A collapse/dedupe would surface as two rows sharing (official_date, home,
    away, game_number). Distinct game_pks always survive (PRIMARY KEY); this
    guards against a same-day pair being flattened onto one game_number.
    """
    bad = _fetch(
        connection,
        """SELECT official_date, home_team_id, away_team_id, game_number, count(*)
           FROM silver.games
           GROUP BY official_date, home_team_id, away_team_id, game_number
           HAVING count(*) > 1
           ORDER BY official_date, home_team_id, away_team_id""",
    )
    return finding(
        "identity.doubleheader_integrity", "P1", [tuple(r) for r in bad],
        "same-day same-team games sharing a game_number",
    )


# --------------------------------------------------------------------------- #
# Bronze integrity
# --------------------------------------------------------------------------- #
def check_bronze_detail_payloads(
    connection: Any, storage_root: str | Path | None
) -> CheckResult:
    """Bronze game-detail payloads: stored hash, raw file, and payload_json all agree.

    Surfaces tampered/missing raw payloads with per-record context (DATA-005
    P3(b)) and payload_json that diverges from the raw bytes (P3(d)), instead of
    an opaque backfill abort.
    """
    if not _table_exists(connection, "bronze", "mlb_game_detail_payloads"):
        return ok("bronze.detail_payload_integrity", "P0", "no game-detail payloads")
    rows = _fetch(
        connection,
        """SELECT game_pk, payload_sha256, raw_path, payload_json
           FROM bronze.mlb_game_detail_payloads ORDER BY game_pk""",
    )
    bad: list[object] = []
    for game_pk, sha, raw_path, payload_json in rows:
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != sha:
            bad.append((game_pk, "payload_json_hash_mismatch"))
            continue
        if storage_root is None:
            continue
        raw_file = Path(storage_root) / "raw" / raw_path
        if not raw_file.is_file():
            bad.append((game_pk, "raw_missing"))
            continue
        if hashlib.sha256(raw_file.read_bytes()).hexdigest() != sha:
            bad.append((game_pk, "raw_hash_mismatch"))
    return finding(
        "bronze.detail_payload_integrity", "P0", bad, "game-detail payloads failing hash/raw integrity"
    )


def check_bronze_schedule_raw(
    connection: Any, storage_root: str | Path | None
) -> CheckResult:
    """Each retained schedule payload must have its raw file present and unaltered."""
    if not _table_exists(connection, "bronze", "mlb_schedule_payloads"):
        return ok("bronze.schedule_raw_present", "P1", "no schedule payloads")
    if storage_root is None:
        return warn(
            "bronze.schedule_raw_present",
            "storage_root not provided; raw schedule files not verified",
        )
    rows = _fetch(
        connection,
        "SELECT payload_sha256, raw_path FROM bronze.mlb_schedule_payloads ORDER BY payload_sha256",
    )
    bad: list[object] = []
    for sha, raw_path in rows:
        raw_file = Path(storage_root) / "raw" / raw_path
        if not raw_file.is_file():
            bad.append((sha, "raw_missing"))
        elif hashlib.sha256(raw_file.read_bytes()).hexdigest() != sha:
            bad.append((sha, "raw_hash_mismatch"))
    return finding(
        "bronze.schedule_raw_present", "P1", bad, "schedule payloads with missing/altered raw file"
    )


def check_bronze_provenance(connection: Any) -> CheckResult:
    """Game-detail rows must carry source/endpoint/run/build provenance."""
    if not _table_exists(connection, "bronze", "mlb_game_detail_payloads"):
        return ok("bronze.provenance", "P1", "no game-detail payloads")
    bad = _fetch(
        connection,
        """SELECT game_pk FROM bronze.mlb_game_detail_payloads
           WHERE source IS NULL OR endpoint IS NULL
              OR ingestion_run_id IS NULL OR ingestion_build_id IS NULL
              OR retrieved_at IS NULL
           ORDER BY game_pk""",
    )
    return finding("bronze.provenance", "P1", _flatten(bad), "game-detail rows missing provenance")


_SILVER_TABLES = (
    "games",
    "team_game_statistics",
    "pitcher_appearances",
    "pitcher_starters",
    "odds_snapshots",
    "odds_event_game_mapping",
)


def check_processing_determinism(connection: Any) -> CheckResult:
    """The committed Silver dataset must be reproducible from Bronze + code.

    Rebuilds Silver from the current Bronze data in an *isolated* in-memory
    scratch database and compares it against the committed Silver tables. A
    divergence means the certified dataset is not a pure, deterministic function
    of raw inputs and code (ADR-001 / data reproducibility rule) — i.e. drift or
    non-determinism.

    Critically, this check is side-effect-free: it never mutates the committed
    Silver being certified. ``certify``/``run_all`` can therefore be run against
    the live certified dataset without rewriting it (and without masking drift by
    overwriting the committed rows with a fresh rebuild).
    """
    from transforms import normalize_silver

    # Snapshot the committed (certified) Silver first, from the live catalog.
    # This is left untouched.
    committed = {
        t: _fetch(connection, f"SELECT * FROM silver.{t} ORDER BY ALL")
        for t in _SILVER_TABLES
    }

    origin = _scalar(connection, "SELECT current_database()")
    scratch = "val_determinism_scratch"
    try:
        connection.execute(f"ATTACH ':memory:' AS {scratch}")
    except Exception as error:  # pragma: no cover - environment/setup failure
        return fail(
            "bronze.processing_determinism", "P1",
            f"could not create isolated rebuild database: {error}",
        )
    try:
        # Copy Bronze (and everything) into scratch, rebuild Silver there, and
        # read it back. All writes land in scratch; the origin catalog is only
        # ever read.
        connection.execute(f'COPY FROM DATABASE "{origin}" TO {scratch}')
        connection.execute(f"USE {scratch}")
        try:
            normalize_silver(connection)
            rebuilt = {
                t: _fetch(connection, f"SELECT * FROM silver.{t} ORDER BY ALL")
                for t in _SILVER_TABLES
            }
        finally:
            connection.execute(f'USE "{origin}"')
    except Exception as error:  # normalization contract violation is itself a finding
        return fail(
            "bronze.processing_determinism", "P1", f"silver rebuild failed: {error}"
        )
    finally:
        try:
            connection.execute(f"DETACH DATABASE {scratch}")
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    diverged = [t for t in _SILVER_TABLES if committed[t] != rebuilt[t]]
    return finding(
        "bronze.processing_determinism", "P1", diverged,
        "silver tables not reproducible from Bronze",
    )


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


def _flatten(rows: list[tuple]) -> list[object]:
    return [row[0] for row in rows]
