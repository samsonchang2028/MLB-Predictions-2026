"""Unit tests for certification PASS/FAIL aggregation (DATA-007).

These exercise the pure aggregation/shaping logic without a database. The
end-to-end artifact generation is covered by the integration tests.
"""

from __future__ import annotations

from validation.certification import (
    CERTIFICATION_VERSION,
    _category,
    _fingerprint,
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


def test_write_certification_is_content_addressed(tmp_path) -> None:
    artifact = {
        "status": "PASS",
        "dataset": {"fingerprint": "abc123"},
        "certified_at": "2026-01-01T00:00:00+00:00",
    }
    path = write_certification(artifact, tmp_path / "certs")
    assert path.name == "certification-PASS-abc123.json"
    assert path.is_file()
