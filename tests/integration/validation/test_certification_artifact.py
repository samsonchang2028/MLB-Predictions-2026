"""Integration tests: generate real PASS/FAIL certification artifacts (DATA-007).

Reuses the DATA-006 Bronze/Silver fixture builder (no network) so certification
runs against the same small dataset the validation suite is proven against.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from storage import connect_database
from validation import certification
from validation.certification import build_certification, certify_and_write
from validation.results import fail, ok

# Reuse the DATA-006 fixture builder (same directory -> importable by name).
from test_dataset_validation import _build_fixture

_FIXED_NOW = datetime(2026, 8, 7, 19, 42, tzinfo=timezone.utc)
_FIXED_CODE_VERSION = {"git_commit": "0" * 40, "git_dirty": False}

_REQUIRED_KEYS = (
    "certification_version",
    "status",
    "dataset",
    "source_versions",
    "source_hashes",
    "row_counts",
    "missingness",
    "duplicate_counts",
    "referential_integrity",
    "lifecycle",
    "pitcher_completeness",
    "temporal",
    "leakage",
    "reconciliation",
    "checks",
    "warnings",
    "failures",
    "merge_blocking",
    "code_version",
    "certified_at",
)


def test_clean_fixture_produces_pass_artifact(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    certs = tmp_path / "state" / "data-certifications"
    with connect_database(database) as connection:
        artifact, path = certify_and_write(
            connection,
            root,
            certs,
            code_version=_FIXED_CODE_VERSION,
            now=_FIXED_NOW,
        )

    assert artifact["status"] == "PASS"
    assert artifact["merge_blocking"] == []
    assert artifact["failures"] == []
    # Every required summary dimension is present.
    for key in _REQUIRED_KEYS:
        assert key in artifact, key
    # Traceability: source hashes and code version must never be omitted.
    assert artifact["source_hashes"]
    assert artifact["code_version"]["git_commit"] == "0" * 40
    assert artifact["dataset"]["seasons"] == ["2024"]
    assert artifact["row_counts"]["games"] >= 1

    # Durable, versioned, PASS-labeled artifact on disk that round-trips.
    assert path.is_file()
    assert path.name.startswith("certification-PASS-")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "PASS"
    assert on_disk["dataset"]["fingerprint"] == artifact["dataset"]["fingerprint"]


def test_certification_is_deterministic_except_timestamp(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with connect_database(database) as connection:
        first = build_certification(
            connection, root, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )
        same = build_certification(
            connection, root, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )
        different_time = build_certification(
            connection, root, code_version=_FIXED_CODE_VERSION, now=later
        )

    # Identical inputs (incl. timestamp) -> byte-identical artifact.
    assert first == same
    # Only certified_at may differ across runs of the same build.
    assert different_time["certified_at"] != first["certified_at"]
    assert {k: v for k, v in different_time.items() if k != "certified_at"} == {
        k: v for k, v in first.items() if k != "certified_at"
    }
    assert different_time["dataset"]["fingerprint"] == first["dataset"]["fingerprint"]


def test_tampered_payload_produces_fail_artifact(tmp_path: Path) -> None:
    root = tmp_path / "data"
    database = _build_fixture(root)
    certs = tmp_path / "state" / "data-certifications"
    raw_files = sorted((root / "raw" / "mlb" / "game-details").rglob("*.json"))
    raw_files[0].write_bytes(b'{"gamePk": 100, "tampered": true}')

    with connect_database(database) as connection:
        artifact, path = certify_and_write(
            connection, root, certs, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )

    assert artifact["status"] == "FAIL"
    assert "bronze.detail_payload_integrity" in artifact["merge_blocking"]
    assert any(
        f["check"] == "bronze.detail_payload_integrity" for f in artifact["failures"]
    )
    assert path.name.startswith("certification-FAIL-")
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "FAIL"


def test_leakage_failure_forces_fail_artifact(tmp_path, monkeypatch) -> None:
    """Regression: a leakage failure must force the certification artifact to FAIL,
    even when all descriptive dataset metadata is otherwise clean."""
    root = tmp_path / "data"
    database = _build_fixture(root)
    certs = tmp_path / "state" / "data-certifications"

    leaked = [
        ok("identity.game_pk_unique", "P0"),
        fail(
            "leakage.future_mutation_invariance",
            "P0",
            "future game altered earlier features",
            [(100, 111)],
        ),
    ]
    # Force run_all (as seen by the certification module) to surface a leak.
    monkeypatch.setattr(certification, "run_all", lambda *a, **k: leaked)

    with connect_database(database) as connection:
        artifact, path = certify_and_write(
            connection, root, certs, code_version=_FIXED_CODE_VERSION, now=_FIXED_NOW
        )

    assert artifact["status"] == "FAIL"
    assert "leakage.future_mutation_invariance" in artifact["merge_blocking"]
    assert artifact["leakage"]["status"] == "FAIL"
    assert path.name.startswith("certification-FAIL-")
