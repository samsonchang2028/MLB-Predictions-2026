"""Versioned PASS/FAIL certification artifact for the historical dataset (DATA-007).

This layer *consumes* the DATA-006 validation surface (``run_all`` / ``summarize``)
and turns it into a durable, machine-readable certification artifact written to a
versioned repository path. It does not re-implement any validation logic and does
not mutate the dataset being certified.

Certification rule (ADR-004): the certification status is ``FAIL`` if any check
failed, and a P0/P1 (merge-blocking) or leakage failure *always* forces ``FAIL``.

Determinism: for a fixed dataset build and code version the artifact is
byte-for-byte reproducible except for the explicit ``certified_at`` timestamp.
The artifact is content-addressed by a ``fingerprint`` derived only from
deterministic inputs, so regenerating the same build yields the same filename.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validation.results import FAIL, PASS, CheckResult, summarize
from validation.runner import run_all
from validation.coverage import coverage_report

# Bump when the artifact schema changes in a backward-incompatible way.
CERTIFICATION_VERSION = 1

# Repository convention for durable certification artifacts (ADR-004).
DEFAULT_CERTIFICATIONS_DIR = "state/data-certifications"

# Silver tables that make up the certified dataset content.
_SILVER_TABLES = (
    "games",
    "team_game_statistics",
    "pitcher_appearances",
    "pitcher_starters",
    "odds_snapshots",
    "odds_event_game_mapping",
)

# Check-id categorization for the human/script-readable summary sections.
# Every check result is always retained under ``checks.results`` regardless.
_TEMPORAL_CHECKS = ("leakage.chronological_folds",)
_LEAKAGE_CHECKS = (
    "leakage.future_mutation_invariance",
    "leakage.current_game_excluded",
)
_LIFECYCLE_CHECKS = (
    "identity.game_status_known",
    "identity.doubleheader_integrity",
    "results.status_consistency",
)
_RECONCILIATION_CHECKS = ("bronze.processing_determinism",)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def certification_status(results: list[CheckResult]) -> str:
    """Aggregate check results into an explicit certification status.

    ``FAIL`` if any check failed; a merge-blocking (P0/P1) or leakage failure
    always forces ``FAIL`` (ADR-004). This is the single source of truth for the
    PASS/FAIL verdict and is pure over its inputs.
    """
    summary = summarize(results)
    if summary["merge_blocking"]:
        return FAIL
    return summary["status"]


def build_certification(
    connection: Any,
    storage_root: str | Path | None = None,
    *,
    code_version: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run validation and assemble the certification artifact.

    ``now`` and ``code_version`` are injectable for deterministic testing; by
    default the run timestamp is captured live and the code version is read from
    git. Everything else is a pure function of the dataset build.
    """
    results = run_all(connection, storage_root)
    summary = summarize(results)
    status = certification_status(results)

    by_check = {r.check: r for r in results}
    artifact = {
        "certification_version": CERTIFICATION_VERSION,
        "status": status,
        "dataset": _dataset_identity(connection),
        "source_versions": _source_versions(connection),
        "source_hashes": _source_hashes(connection),
        "row_counts": _row_counts(connection),
        "missingness": _missingness(connection),
        "duplicate_counts": _duplicate_counts(connection),
        "referential_integrity": _category(results, prefix="ref."),
        "lifecycle": _category(results, names=_LIFECYCLE_CHECKS),
        "pitcher_completeness": _pitcher_completeness(connection, results),
        "semantic_completeness": _semantic_completeness(connection, results),
        "validity": _validity_dimensions(results),
        "temporal": _category(results, names=_TEMPORAL_CHECKS),
        "leakage": _category(results, names=_LEAKAGE_CHECKS),
        "reconciliation": _reconciliation(connection, by_check),
        "checks": {
            "summary": summary,
            "results": [_result_dict(r) for r in results],
        },
        "warnings": [_result_dict(r) for r in results if r.status == "WARN"],
        "failures": [_result_dict(r) for r in results if r.status == FAIL],
        "merge_blocking": list(summary["merge_blocking"]),
        "code_version": code_version if code_version is not None else _code_version(),
        "certified_at": (now or datetime.now(timezone.utc)).isoformat(),
    }
    artifact["dataset"]["fingerprint"] = _fingerprint(artifact)
    return artifact


def write_certification(
    artifact: dict[str, Any], certifications_dir: str | Path
) -> Path:
    """Write the artifact to a content-addressed, versioned JSON file.

    The filename is derived from the deterministic dataset fingerprint, so
    re-certifying the same build overwrites the same file with identical bytes
    (except ``certified_at``). Different builds produce distinct retained files.
    """
    directory = Path(certifications_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fingerprint = artifact["dataset"]["fingerprint"]
    path = directory / f"certification-{artifact['status']}-{fingerprint}.json"
    path.write_text(_canonical_json(artifact) + "\n", encoding="utf-8")
    return path


def certify_and_write(
    connection: Any,
    storage_root: str | Path | None,
    certifications_dir: str | Path,
    *,
    code_version: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    """Build the certification artifact and persist it. Returns (artifact, path)."""
    artifact = build_certification(
        connection, storage_root, code_version=code_version, now=now
    )
    return artifact, write_certification(artifact, certifications_dir)


# --------------------------------------------------------------------------- #
# Result shaping
# --------------------------------------------------------------------------- #
def _result_dict(result: CheckResult) -> dict[str, Any]:
    return {
        "check": result.check,
        "status": result.status,
        "severity": result.severity,
        "message": result.message,
        "failing": [_jsonable(item) for item in result.failing],
        "blocking": result.blocking,
    }


def _category(
    results: list[CheckResult],
    *,
    prefix: str | None = None,
    names: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Group a subset of check results into a status-bearing summary block."""
    selected = [
        r
        for r in results
        if (prefix is not None and r.check.startswith(prefix)) or r.check in names
    ]
    failed = [r for r in selected if r.status == FAIL]
    return {
        "status": FAIL if failed else PASS,
        "checks": [_result_dict(r) for r in selected],
    }


# --------------------------------------------------------------------------- #
# Three validity dimensions (ADR-005): structural, semantic, temporal/leakage.
# All three are required for PASS; reporting them separately makes it visible
# which dimension failed. The overall verdict still comes from
# ``certification_status`` over ALL results, so a merge-blocking semantic FAIL
# forces the whole certification to FAIL.
# --------------------------------------------------------------------------- #
_SEMANTIC_PREFIX = "semantic."
_TEMPORAL_LEAKAGE_PREFIX = "leakage."


def _semantic_completeness(
    connection: Any, results: list[CheckResult]
) -> dict[str, Any]:
    """Semantic-completeness dimension: coverage numbers + check statuses.

    Records per-column coverage and per-family usability (DATA-017) so builds are
    comparable over time, alongside the ``semantic.*`` check verdicts.
    """
    report = coverage_report(connection)
    semantic_results = [
        r for r in results if r.check.startswith(_SEMANTIC_PREFIX)
    ]
    report["status"] = FAIL if any(
        r.status == FAIL for r in semantic_results
    ) else PASS
    report["checks"] = [_result_dict(r) for r in semantic_results]
    return report


def _validity_dimensions(results: list[CheckResult]) -> dict[str, Any]:
    """Group every check under exactly one of the three validity dimensions."""

    def dimension(predicate: Any) -> dict[str, Any]:
        selected = [r for r in results if predicate(r.check)]
        return {
            "status": FAIL if any(r.status == FAIL for r in selected) else PASS,
            "checks": [r.check for r in selected],
        }

    return {
        "structural": dimension(
            lambda c: not c.startswith(
                (_SEMANTIC_PREFIX, _TEMPORAL_LEAKAGE_PREFIX)
            )
        ),
        "semantic_completeness": dimension(
            lambda c: c.startswith(_SEMANTIC_PREFIX)
        ),
        "temporal_leakage": dimension(
            lambda c: c.startswith(_TEMPORAL_LEAKAGE_PREFIX)
        ),
    }


# --------------------------------------------------------------------------- #
# Dataset metadata (descriptive; never mutates the dataset)
# --------------------------------------------------------------------------- #
def _dataset_identity(connection: Any) -> dict[str, Any]:
    seasons = [
        row[0]
        for row in _fetch(
            connection,
            "SELECT DISTINCT season FROM silver.games "
            "WHERE season IS NOT NULL ORDER BY season",
        )
    ]
    game_types = [
        row[0]
        for row in _fetch(
            connection,
            "SELECT DISTINCT game_type FROM silver.games "
            "WHERE game_type IS NOT NULL ORDER BY game_type",
        )
    ]
    return {
        "database": _scalar(connection, "SELECT current_database()"),
        "seasons": seasons,
        "game_types": game_types,
        "dataset_content_hash": _dataset_content_hash(connection),
    }


def _source_versions(connection: Any) -> dict[str, Any]:
    """Ingestion provenance identifying which source build produced the data."""
    versions: dict[str, Any] = {}
    if _table_exists(connection, "bronze", "mlb_game_detail_payloads"):
        rows = _fetch(
            connection,
            """SELECT DISTINCT source, endpoint, ingestion_run_id, ingestion_build_id
               FROM bronze.mlb_game_detail_payloads
               ORDER BY source, endpoint, ingestion_run_id, ingestion_build_id""",
        )
        versions["mlb_game_detail"] = [
            {
                "source": s,
                "endpoint": e,
                "ingestion_run_id": run,
                "ingestion_build_id": build,
            }
            for s, e, run, build in rows
        ]
    if _table_exists(connection, "bronze", "odds_moneyline_snapshots"):
        versions["odds_snapshots_sources"] = [
            row[0]
            for row in _fetch(
                connection,
                "SELECT DISTINCT source FROM bronze.odds_moneyline_snapshots "
                "ORDER BY source",
            )
        ]
    if _table_exists(connection, "bronze", "historical_odds_archive_artifacts"):
        versions["historical_odds_archive"] = [
            dict(zip(("artifact", "sha256"), row))
            for row in _fetch(
                connection,
                "SELECT * FROM bronze.historical_odds_archive_artifacts ORDER BY ALL",
            )
        ]
    return versions


def _source_hashes(connection: Any) -> dict[str, Any]:
    """Bounded, deterministic source-hash provenance per Bronze table.

    Rather than dumping every payload hash, record the row count and a stable
    aggregate hash of the sorted distinct payload SHA-256s. This preserves
    traceability (source hashes -> certification) while keeping the artifact
    small, and any change to source payloads changes the aggregate.
    """
    hashes: dict[str, Any] = {}
    for schema, table, column in (
        ("bronze", "mlb_games", "payload_sha256"),
        ("bronze", "mlb_schedule_payloads", "payload_sha256"),
        ("bronze", "mlb_game_detail_payloads", "payload_sha256"),
    ):
        if not _table_exists(connection, schema, table):
            continue
        shas = [
            row[0]
            for row in _fetch(
                connection,
                f"SELECT DISTINCT {column} FROM {schema}.{table} "
                f"WHERE {column} IS NOT NULL ORDER BY {column}",
            )
        ]
        hashes[table] = {"distinct": len(shas), "aggregate_sha256": _hash_strings(shas)}
    return hashes


def _row_counts(connection: Any) -> dict[str, int]:
    return {
        table: _scalar(connection, f"SELECT count(*) FROM silver.{table}")
        for table in _SILVER_TABLES
        if _table_exists(connection, "silver", table)
    }


def _missingness(connection: Any) -> dict[str, Any]:
    """Null counts for identity-critical columns (not a pass/fail check itself)."""
    missing: dict[str, Any] = {}
    if _table_exists(connection, "silver", "games"):
        missing["games"] = _null_counts(
            connection,
            "silver.games",
            (
                "season",
                "game_date",
                "official_date",
                "home_team_id",
                "away_team_id",
                "abstract_game_state",
            ),
        )
    if _table_exists(connection, "silver", "team_game_statistics"):
        missing["team_game_statistics_final"] = _null_counts(
            connection,
            "silver.team_game_statistics t "
            "JOIN silver.games g USING (game_pk)",
            ("score", "is_winner"),
            where="g.abstract_game_state = 'Final'",
            qualify="t.",
        )
    return missing


def _duplicate_counts(connection: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if _table_exists(connection, "silver", "games"):
        counts["games_by_game_pk"] = _scalar(
            connection,
            "SELECT count(*) FROM (SELECT game_pk FROM silver.games "
            "GROUP BY game_pk HAVING count(*) > 1)",
        )
    if _table_exists(connection, "silver", "team_game_statistics"):
        counts["team_game_statistics_by_key"] = _scalar(
            connection,
            "SELECT count(*) FROM (SELECT game_pk, team_id "
            "FROM silver.team_game_statistics "
            "GROUP BY game_pk, team_id HAVING count(*) > 1)",
        )
    return counts


def _pitcher_completeness(
    connection: Any, results: list[CheckResult]
) -> dict[str, Any]:
    """Completeness of pitcher data for regular-season Final games + check status."""
    block = _category(results, prefix="pitching.")
    # Fold in the pitcher referential-integrity checks so completeness is holistic.
    block["checks"].extend(
        _result_dict(r) for r in results if r.check.startswith("ref.pitcher")
    )
    if any(
        r["status"] == FAIL for r in block["checks"]
    ):  # keep status consistent after extend
        block["status"] = FAIL
    if _table_exists(connection, "silver", "games") and _table_exists(
        connection, "silver", "pitcher_appearances"
    ):
        block["regular_final_games"] = _scalar(
            connection,
            "SELECT count(*) FROM silver.games "
            "WHERE game_type = 'R' AND abstract_game_state = 'Final'",
        )
        block["regular_final_games_missing_appearances"] = _scalar(
            connection,
            """SELECT count(*) FROM silver.games g
               WHERE g.game_type = 'R' AND g.abstract_game_state = 'Final'
                 AND NOT EXISTS (
                     SELECT 1 FROM silver.pitcher_appearances a
                     WHERE a.game_pk = g.game_pk)""",
        )
    return block


def _reconciliation(connection: Any, by_check: dict[str, CheckResult]) -> dict[str, Any]:
    """Silver<-Bronze determinism plus odds event->game mapping reconciliation."""
    recon: dict[str, Any] = {}
    for name in _RECONCILIATION_CHECKS:
        if name in by_check:
            recon[name] = _result_dict(by_check[name])
    if _table_exists(connection, "silver", "odds_event_game_mapping"):
        recon["odds_mapping"] = {
            status: count
            for status, count in _fetch(
                connection,
                "SELECT mapping_status, count(*) FROM silver.odds_event_game_mapping "
                "GROUP BY mapping_status ORDER BY mapping_status",
            )
        }
    return recon


def _dataset_content_hash(connection: Any) -> str:
    """Stable hash over the committed Silver contents (order-normalized)."""
    parts: list[str] = []
    for table in _SILVER_TABLES:
        if not _table_exists(connection, "silver", table):
            parts.append(f"{table}:absent")
            continue
        rows = _fetch(connection, f"SELECT * FROM silver.{table} ORDER BY ALL")
        parts.append(f"{table}:" + _hash_strings([repr(row) for row in rows]))
    return _hash_strings(parts)


# --------------------------------------------------------------------------- #
# Code version + fingerprint
# --------------------------------------------------------------------------- #
def _code_version(root: str | Path | None = None) -> dict[str, Any]:
    """Best-effort git commit/dirty state. Always returns a populated dict.

    The certification must record a code version (merge-blocking if omitted); if
    git is unavailable the commit is recorded as ``"unknown"`` rather than dropped.
    """
    repo_root = Path(root) if root is not None else Path(__file__).resolve().parent
    commit = _git(repo_root, "rev-parse", "HEAD") or "unknown"
    dirty_output = _git(repo_root, "status", "--porcelain")
    return {
        "git_commit": commit,
        "git_dirty": bool(dirty_output) if dirty_output is not None else None,
    }


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _fingerprint(artifact: dict[str, Any]) -> str:
    """Content-address the artifact over its deterministic parts only.

    Excludes ``certified_at`` (the only non-deterministic field) and the
    fingerprint slot itself, so the same build always yields the same id.
    """
    deterministic = {k: v for k, v in artifact.items() if k != "certified_at"}
    return hashlib.sha256(
        _canonical_json(deterministic).encode("utf-8")
    ).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _jsonable(item: Any) -> Any:
    if isinstance(item, (list, tuple)):
        return [_jsonable(sub) for sub in item]
    if isinstance(item, (str, int, float, bool)) or item is None:
        return item
    return repr(item)


def _hash_strings(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _null_counts(
    connection: Any,
    source: str,
    columns: tuple[str, ...],
    *,
    where: str = "",
    qualify: str = "",
) -> dict[str, int]:
    clause = f" WHERE {where}" if where else ""
    selects = ", ".join(
        f"count(*) FILTER (WHERE {qualify}{col} IS NULL)" for col in columns
    )
    row = _fetch(connection, f"SELECT {selects} FROM {source}{clause}")[0]
    return dict(zip(columns, row))


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
