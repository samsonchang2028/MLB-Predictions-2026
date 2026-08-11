"""Unit tests for certification PASS/FAIL aggregation (DATA-007).

These exercise the pure aggregation/shaping logic without a database. The
end-to-end artifact generation is covered by the integration tests.
"""

from __future__ import annotations

from validation.certification import (
    CERTIFICATION_VERSION,
    _category,
    _dimension_status,
    _fingerprint,
    _validity_dimensions,
    certification_status,
    write_certification,
)
from validation.results import fail, ok, warn


def test_clean_results_certify_pass() -> None:
    results = [ok("a", "P0"), warn("b", "note"), ok("c", "P1")]
    assert certification_status(results) == "PASS"


def test_any_failure_forces_fail() -> None:
    # Even a soft P2 failure means the dataset did not fully pass.
    results = [ok("a", "P0"), fail("c", "P2", "soft fail")]
    assert certification_status(results) == "FAIL"


def test_p0_p1_failure_forces_fail() -> None:
    assert certification_status([ok("a", "P0"), fail("d", "P1", "hard")]) == "FAIL"
    assert certification_status([ok("a", "P0"), fail("d", "P0", "hard")]) == "FAIL"


def test_leakage_failure_forces_fail() -> None:
    # Leakage checks are severity P0 -> merge-blocking -> forced FAIL.
    results = [
        ok("identity.game_pk_unique", "P0"),
        fail("leakage.future_mutation_invariance", "P0", "leak", [(1, 2)]),
    ]
    assert certification_status(results) == "FAIL"


def test_category_groups_by_prefix_and_names() -> None:
    results = [
        ok("ref.team_statistics_game", "P0"),
        fail("ref.pitcher_appearance_game", "P0", "orphans", [1]),
        ok("identity.game_pk_unique", "P0"),
    ]
    block = _category(results, prefix="ref.")
    assert block["status"] == "FAIL"
    assert {c["check"] for c in block["checks"]} == {
        "ref.team_statistics_game",
        "ref.pitcher_appearance_game",
    }


def test_category_named_selection_passes_when_no_failures() -> None:
    results = [ok("results.status_consistency", "P1"), ok("other", "P2")]
    block = _category(results, names=("results.status_consistency",))
    assert block["status"] == "PASS"
    assert [c["check"] for c in block["checks"]] == ["results.status_consistency"]


def test_fingerprint_ignores_timestamp_only() -> None:
    base = {
        "certification_version": CERTIFICATION_VERSION,
        "status": "PASS",
        "dataset": {"seasons": ["2024"]},
        "certified_at": "2026-01-01T00:00:00+00:00",
    }
    other_time = dict(base, certified_at="2030-12-31T23:59:59+00:00")
    changed = dict(base, status="FAIL")
    assert _fingerprint(base) == _fingerprint(other_time)
    assert _fingerprint(base) != _fingerprint(changed)


# --------------------------------------------------------------------------- #
# DATA-017 repair (731833e): a validity dimension with WARN-only checks (no
# FAIL) must report WARN, not silently collapse to PASS and not be escalated
# to FAIL. Pre-repair, ``_semantic_completeness``/``_validity_dimensions`` used
# ``FAIL if any FAIL else PASS`` with no WARN branch, so a WARN-only semantic
# dimension incorrectly reported PASS.
# --------------------------------------------------------------------------- #
def test_dimension_status_warn_only_is_warn_not_pass() -> None:
    results = [ok("semantic.a", "P1"), warn("semantic.b", "partial coverage")]
    assert _dimension_status(results) == "WARN"


def test_dimension_status_fail_beats_warn() -> None:
    results = [warn("semantic.a", "note"), fail("semantic.b", "P1", "hard fail")]
    assert _dimension_status(results) == "FAIL"


def test_dimension_status_all_pass_is_pass() -> None:
    assert _dimension_status([ok("semantic.a", "P1"), ok("semantic.b", "P1")]) == "PASS"


def test_validity_dimensions_warn_only_semantic_not_collapsed_to_pass() -> None:
    """Regression for the DATA-017 WARN-collapse bug at the dimension-report
    level: a WARN-only semantic-completeness dimension must surface as WARN,
    with structural/temporal_leakage unaffected."""
    results = [
        ok("identity.game_pk_unique", "P0"),
        warn("semantic.pitcher_stat_coverage", "null_rate=0.030 (partial coverage)"),
        ok("leakage.chronological_folds", "P0"),
    ]
    dims = _validity_dimensions(results)
    assert dims["semantic_completeness"]["status"] == "WARN"
    assert dims["structural"]["status"] == "PASS"
    assert dims["temporal_leakage"]["status"] == "PASS"


def test_write_certification_is_content_addressed(tmp_path) -> None:
    artifact = {
        "status": "PASS",
        "dataset": {"fingerprint": "abc123"},
        "certified_at": "2026-01-01T00:00:00+00:00",
    }
    path = write_certification(artifact, tmp_path / "certs")
    assert path.name == "certification-PASS-abc123.json"
    assert path.is_file()
