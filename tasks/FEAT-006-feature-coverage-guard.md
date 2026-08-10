# FEAT-006 — Feature Matrix Must Not Silently Publish Dead Columns

## Status

ready

## Dependencies

- FEAT-004

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Rationale

The first real experiment trained on a matrix where **58 of 211 feature columns
were entirely empty** (every starter rolling/season/rest feature and every bullpen
ERA/WHIP), caused by DATA-016. `build_feature_matrix` derives its column schema
from observed component keys, so an all-`None` column is indistinguishable from a
legitimate one and the matrix published happily. The models silently trained on a
crippled feature set and the metrics looked plausible.

This is the last line of defense: even if ingestion and certification both regress
again, the feature layer must refuse to hand a hollow matrix to the models.

Diagnostic signature worth preserving: every column was either 100% filled or 0%
filled (no partial), which is the fingerprint of a structural upstream failure
rather than sparse data.

## Goal

Make feature coverage explicit, reported, and enforceable.

## Requirements

- Compute per-column coverage (non-null rate) for the published feature columns
  and include it in the matrix result (e.g. `feature_coverage`), so callers and
  reports can see it without recomputation.
- A feature column with ZERO observed values across the whole build must not pass
  silently. Default behavior should FAIL the build with the offending column names
  (they indicate an upstream defect), with an explicit, documented opt-out
  parameter for legitimate cold-start/small-slate inference builds where sparse
  coverage is expected.
- Distinguish the build-time case (full historical matrix - dead column = defect)
  from the inference case (a one-day slate legitimately lacks history), so the
  PIPE-001 declared-column-union path is not broken by this guard.
- Do not change the existing feature semantics, target isolation, ordering, or
  the declared-column-union contract PIPE-001 depends on.

## Acceptance criteria

- A matrix build containing an all-empty feature column fails with that column
  named, and the message points at upstream data rather than the feature code.
- Coverage is exposed in the result and covered by tests.
- A legitimately sparse inference build still works via the documented opt-out.
- Existing FEAT-004 tests and the PIPE-001 pipeline tests remain green.
