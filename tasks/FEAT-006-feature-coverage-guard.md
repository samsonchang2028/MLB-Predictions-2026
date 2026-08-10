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

Add a GOLD PRE-MODEL COMPLETENESS GATE: before ML experiments may run, the Gold
feature matrix must produce a feature-completeness report, and a required feature
family that is effectively absent must BLOCK the experiments.

## Requirements

- Compute per-column coverage (non-null rate) for the published feature columns
  and include it in the matrix result (e.g. `feature_coverage`), so callers and
  reports can see it without recomputation.
- Produce a FAMILY-LEVEL report over the declared V1 families - at minimum
  **team, starter, bullpen, rest/schedule** - reporting, per family, required
  columns present and population status, conceptually:

  ```
  TEAM FEATURES
    required columns ........ PASS
    population .............. PASS

  STARTER FEATURES
    required columns ........ PASS/FAIL
    population .............. PASS/FAIL

  BULLPEN FEATURES
    required columns ........ PASS/FAIL
    population .............. PASS/FAIL

  REST FEATURES
    required columns ........ PASS/FAIL
    population .............. PASS/FAIL
  ```

- The gate must DETECT: entirely null feature columns; entirely missing required
  feature families; unexpected CONSTANT columns; feature families with
  implausibly low population; and unresolved missingness that would cause the
  model to effectively ignore a planned feature family.
- Any required feature family that is effectively absent must BLOCK ML
  experiments (fail the gate).
- **DO NOT solve this by automatically dropping broken features.** A broken
  planned feature family must be repaired, or explicitly removed from project
  methodology through an accepted ADR. Auto-dropping is forbidden because it hides
  the defect - exactly how this incident went unnoticed.
- Declare the required families/columns explicitly in code and document the
  population thresholds with reasons (legitimate sparsity, e.g. early-2021
  starters lacking prior-season history, must not be treated as failure).
- Distinguish the build-time case (full historical matrix - dead column = defect)
  from the inference case (a one-day slate legitimately lacks history), so the
  PIPE-001 declared-column-union path is not broken by this guard.
- Do not change the existing feature semantics, target isolation, ordering, or
  the declared-column-union contract PIPE-001 depends on.

## Acceptance criteria

- A matrix build containing an all-empty feature column or an absent required
  family FAILS the gate, naming the column(s)/family and pointing at upstream data
  rather than the feature code.
- The family-level completeness report is produced, exposed, and tested.
- A constant column is detected.
- Legitimate sparsity does not fail; thresholds are documented with reasons.
- A legitimately sparse inference build still works via the documented path.
- Existing FEAT-004 tests and the PIPE-001 pipeline tests remain green.
