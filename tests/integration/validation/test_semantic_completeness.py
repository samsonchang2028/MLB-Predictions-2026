"""Integration tests: semantic-completeness dimension in certification (DATA-017).

Reuses the DATA-006 Bronze/Silver fixture builder (no network). Proves that:

- a clean build reports the semantic-completeness dimension PASS and records
  per-column coverage numbers in the artifact, and
- the DATA-016 hollow build (pitcher stat line 100% NULL) — which previously
  certified PASS — now FAILs certification on the semantic dimension while the
  structural and temporal/leakage dimensions still report their own status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from storage import connect_database
from validation import certify
from validation.certification import build_certification
from validation.coverage import run_coverage_checks

# Reuse the DATA-006 fixture builder (same integration dir -> importable).
from test_dataset_validation import _build_fixture

_FIXED_NOW = datetime(2026, 8, 10, 19, 42, tzinfo=timezone.utc)
_FIXED_CODE_VERSION = {"git_commit": "0" * 40, "git_dirty": False}

# The exact columns the hollow 2021-2025 build left 100% NULL (DATA-016).
_HOLLOW_STAT_COLUMNS = (
    "innings_pitched",
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


def _by_check(results) -> dict:
    return {r.check: r for r in results}


def test_clean_fixture_semantic_dimension_passes_and_records_coverage(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        artifact = build_certification(
            connection, root, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )

    assert artifact["status"] == "PASS"

    # Three validity dimensions reported separately, each PASS.
    validity = artifact["validity"]
    assert set(validity) == {"structural", "semantic_completeness", "temporal_leakage"}
    for dimension in validity.values():
        assert dimension["status"] == "PASS"

    # Semantic completeness records per-column coverage for the artifact.
    semantic = artifact["semantic_completeness"]
    assert semantic["status"] == "PASS"
    outs = semantic["columns"]["silver.pitcher_appearances.outs_recorded"]
    assert outs["required"] is True
    assert outs["non_null"] == outs["row_count"] > 0
    assert outs["null_rate"] == 0.0
    assert outs["status"] == "PASS"
    # Family reports exist for the required V1 families.
    assert set(semantic["families"]) >= {"team", "starter", "bullpen", "rest_schedule"}
    for name in ("team", "starter", "bullpen", "rest_schedule"):
        family = semantic["families"][name]
        assert family["row_count"] == family["non_null_count"]
        assert family["status"] == "PASS"


def test_hollow_build_fails_certification_on_semantic_dimension(tmp_path: Path) -> None:
    """The DATA-016 defect: pitcher stat line 100% NULL must now FAIL.

    The semantic-completeness layer catches the hollow columns independently of
    the structural checks: ``run_coverage_checks`` operates only on the committed
    Silver content, so its FAIL does not depend on any other gate.
    """
    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        set_null = ", ".join(f"{c} = NULL" for c in _HOLLOW_STAT_COLUMNS)
        connection.execute(f"UPDATE silver.pitcher_appearances SET {set_null}")

        # Isolated semantic catch (independent of Bronze / determinism).
        coverage = _by_check(run_coverage_checks(connection))
        summary = certify(connection, storage_root=root)
        artifact = build_certification(
            connection, root, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )

    stat_check = coverage["semantic.pitcher_stat_coverage"]
    assert stat_check.status == "FAIL"
    assert stat_check.blocking is True
    assert "silver.pitcher_appearances.outs_recorded" in stat_check.message
    # Starter inputs are now unusable too (defense in depth).
    assert coverage["semantic.feature_family_starter"].status == "FAIL"

    # Whole certification FAILs and the semantic dimension is a failing one.
    assert summary["status"] == "FAIL"
    assert artifact["status"] == "FAIL"
    assert artifact["validity"]["semantic_completeness"]["status"] == "FAIL"
    assert "semantic.pitcher_stat_coverage" in summary["merge_blocking"]


def test_semantic_layer_catches_hollow_content_that_structure_cannot(tmp_path: Path) -> None:
    """Structure/identity/temporal checks are content-blind by design.

    ``check_innings_domain`` and friends accept NULL stat values (they only
    reject malformed/negative values), so the ONLY gate that rejects a hollow
    stat line is the semantic-completeness dimension. This is the DATA-016 gap.
    """
    from validation.checks import check_innings_domain

    root = tmp_path / "data"
    database = _build_fixture(root)
    with connect_database(database) as connection:
        set_null = ", ".join(f"{c} = NULL" for c in _HOLLOW_STAT_COLUMNS)
        connection.execute(f"UPDATE silver.pitcher_appearances SET {set_null}")
        # Structural stat-domain check is content-blind: NULLs are accepted.
        assert check_innings_domain(connection).status == "PASS"
        # The semantic dimension is what rejects the hollow content.
        coverage = _by_check(run_coverage_checks(connection))
    assert coverage["semantic.pitcher_stat_coverage"].status == "FAIL"
