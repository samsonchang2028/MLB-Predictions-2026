# DATA-007 - Historical MLB Data Certification Gate

## Status

blocked

## Dependencies

- DATA-006

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Produce a versioned PASS/FAIL certification artifact for the 2021-2025
historical MLB dataset build and gate downstream feature/model readiness on it.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-003-validation.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `tasks/DATA-006-historical-data-validation.md`

## Allowed files

- `src/validation/`
- `tests/unit/validation/`
- `tests/integration/validation/`
- `state/data-certifications/` if no better convention exists
- `reports/data-quality/` if useful for human-readable detail

## Inputs

- DATA-006 validation runner/results.
- Bronze/Silver dataset build metadata.
- Repository code version.

## Outputs

- Versioned certification artifact with explicit `PASS` or `FAIL`.
- Machine-readable summary sufficient for task readiness decisions.

## Requirements

Certification must summarize at least:

- dataset/build identity,
- seasons covered,
- source versions,
- relevant hashes,
- row counts,
- missingness,
- duplicate counts,
- referential integrity,
- game lifecycle checks,
- pitcher completeness,
- temporal tests,
- leakage tests,
- reconciliation results,
- warnings,
- failures,
- code/git version,
- certification timestamp,
- certification status.

## Critical correctness constraints

- Certification is not just console output.
- A failed certification prevents dependent feature/model tasks from being
  marked ready.
- P0/P1 data findings and leakage failures are merge-blocking.
- Certification must preserve enough traceability for model artifact -> Gold
  feature matrix -> Silver build -> Bronze source data -> certification result
  -> source hashes/code version.

## Acceptance criteria

- Certification can be regenerated deterministically for the same dataset build
  except for explicit run timestamp fields.
- PASS/FAIL status is explicit and easy for humans and scripts to inspect.
- FAIL includes actionable failing checks and affected record context.
- Artifact paths follow repository conventions or use a minimal new convention
  documented in the task handoff.

## Required tests

- Unit tests for PASS/FAIL aggregation.
- Integration tests producing PASS and FAIL certification artifacts from small
  fixtures.
- Regression test proving a leakage failure forces `FAIL`.

## Merge-blocking conditions

- Certification can pass with any P0/P1 validation or leakage failure.
- Certification artifact omits source hashes or code version.
- Certification result is not durable in a versioned repository path.

## Handoff

Record artifact path/schema, commands run, sample PASS/FAIL outputs, test
results, and any project-state updates needed to unblock FEAT-002/FEAT-003.
