# Data Certification Artifacts (DATA-007)

Durable, versioned PASS/FAIL certification artifacts for the historical MLB
dataset build. Required by ADR-004: certification is a versioned artifact, not
console output, and P0/P1 data findings and leakage failures are merge-blocking.

## How to generate

```python
from storage import connect_database
from validation import certify_and_write

with connect_database(database_path) as connection:
    artifact, path = certify_and_write(
        connection,
        storage_root,                       # for raw-payload hash verification
        "state/data-certifications",        # this directory
    )
# artifact["status"] is "PASS" or "FAIL"; `path` is the written JSON file.
```

`build_certification(connection, storage_root)` returns the same artifact dict
without writing it. Both consume the DATA-006 validation runner (`run_all` /
`summarize`) and never mutate the dataset being certified.

## File naming

`certification-<STATUS>-<fingerprint>.json`

- `STATUS` is `PASS` or `FAIL` (grep-friendly).
- `fingerprint` is the first 16 hex chars of a SHA-256 over the artifact's
  deterministic fields (everything except `certified_at`). The same dataset
  build + code version always yields the same fingerprint, so re-certifying is
  idempotent and different builds are retained side by side.

## Determinism

For a fixed dataset build and code version, the artifact is byte-for-byte
reproducible except for the explicit `certified_at` field. `certified_at` and
`code_version` are injectable (`now=`, `code_version=`) for reproducible runs.

## PASS/FAIL rule

`status` is `FAIL` when any check failed. A merge-blocking (P0/P1) or leakage
failure always forces `FAIL` and is listed in `merge_blocking`. `PASS` is only
possible with zero failures.

## Schema (certification_version = 1)

Top-level keys:

- `certification_version` — schema version integer.
- `status` — `PASS` | `FAIL`.
- `dataset` — `database`, `seasons`, `game_types`, `dataset_content_hash`,
  `fingerprint`.
- `source_versions` — MLB game-detail ingestion provenance
  (`source`/`endpoint`/`ingestion_run_id`/`ingestion_build_id`), odds sources,
  historical odds archive artifacts (when present).
- `source_hashes` — per Bronze table: distinct payload count + stable aggregate
  SHA-256 (traceability without dumping every hash).
- `row_counts` — per Silver table row counts.
- `missingness` — null counts for identity-critical columns.
- `duplicate_counts` — duplicate-key counts (expected 0).
- `referential_integrity`, `lifecycle`, `pitcher_completeness`, `temporal`,
  `leakage`, `reconciliation` — status-bearing groupings of the relevant
  checks (plus descriptive completeness/mapping counts).
- `checks` — `summary` (from `summarize`) and the full `results` list.
- `warnings`, `failures` — filtered result lists.
- `merge_blocking` — check ids of P0/P1 (and leakage) failures.
- `code_version` — `git_commit`, `git_dirty`.
- `certified_at` — ISO-8601 UTC run timestamp (the only non-deterministic field).

Committed `certification-*.json` files (produced against a real 2021-2025 build)
are the durable gate downstream FEAT/ML tasks read to confirm the dataset passed.
