# DATA-017 — Certification Must Fail on Structurally Empty Columns

## Status

ready

## Dependencies

- DATA-007

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Rationale

The 2021-2025 build certified **PASS** while every stat column in
`silver.pitcher_appearances` was 100% NULL (see DATA-016). Certification checked
structure, identity, determinism, and derivation, but nothing asserted that
substantive measure columns actually contain values. A hollow dataset therefore
passed a merge-blocking gate and silently produced 58 fully-empty feature
columns, which was only discovered when the first real experiment was run.

## Goal

Add a completeness/coverage check so a structurally empty column cannot certify
PASS.

## Certification must distinguish three kinds of validity

All three are required for PASS; none is sufficient alone:

1. **structural validity** - schema, keys, joins, cardinality, determinism
   (already covered),
2. **semantic completeness** - required measurement content is demonstrably
   present and plausible (THIS TASK),
3. **temporal/leakage validity** - point-in-time correctness (already covered).

Report each dimension separately so a future reader can see which one failed.

## Requirements

- Add a check (e.g. `silver.column_coverage`) that reports the non-null rate for
  the declared measure columns of the Silver tables, at minimum
  `silver.pitcher_appearances` (stat line), `silver.team_game_statistics`
  (`score`, `is_winner`).
- A declared measure column that is 100% NULL is MERGE-BLOCKING (P0/P1), not an
  advisory WARN. A column below a documented coverage threshold is at least a
  WARN with the observed rate reported.
- Certification must FAIL when: a required feature/source column is 100% NULL; a
  declared required feature family is entirely empty; required pitcher-stat
  columns are structurally present but contain no measurements; starter feature
  inputs have no usable data; bullpen feature inputs have no usable data.
- REQUIRED FEATURE FAMILY REPORTS covering at minimum **team, starter, bullpen,
  rest/schedule**, plus the other currently declared V1 groups. Per field (or per
  family where appropriate) expose: row count, non-null count, null rate,
  min/max where meaningful, basic distribution sanity information, and a
  PASS/WARN/FAIL status.
- The expected columns/families must be declared explicitly in code (an
  allowlist), so a newly added column is either declared or intentionally
  excluded - do not infer silently.
- DOCUMENT EVERY THRESHOLD. Do not use arbitrary null thresholds without
  recording why they are valid. Some missing values are LEGITIMATE (e.g. a
  starter with no prior-season history early in 2021), so the goal is detecting
  impossible or clearly broken population patterns - NOT requiring every field to
  be 100% populated.
- **100% NULL on any required measurement column must hard-fail certification
  unless that column is explicitly documented as optional.**
- POPULATED-BUT-BROKEN detection where practical: e.g. a required numeric stat
  that is constant (or constant zero) across essentially the entire historical
  dataset, or a measure outside a documented plausible range (home-win rate in
  [0.45, 0.60]; per-appearance outs/earned runs within sane bounds). Record
  observed values so drift is visible build over build.
- Keep these checks MINIMAL and BASEBALL-SPECIFIC. Do not build a generic
  data-quality framework.
- Keep the check deterministic and side-effect free, consistent with the existing
  certification checks, and include the coverage numbers in the artifact so
  future builds are comparable.
- Do not weaken any existing check.

## Acceptance criteria

- A fixture dataset whose stat column is entirely NULL FAILS certification with a
  clear reason naming the table/column.
- A fully populated fixture passes.
- Partial coverage is reported with its rate.
- The certification artifact records per-column coverage.
