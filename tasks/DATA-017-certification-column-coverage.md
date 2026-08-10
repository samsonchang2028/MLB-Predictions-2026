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

## Requirements

- Add a check (e.g. `silver.column_coverage`) that reports the non-null rate for
  the declared measure columns of the Silver tables, at minimum
  `silver.pitcher_appearances` (stat line), `silver.team_game_statistics`
  (`score`, `is_winner`).
- A declared measure column that is 100% NULL is MERGE-BLOCKING (P0/P1), not an
  advisory WARN. A column below a documented coverage threshold is at least a
  WARN with the observed rate reported.
- The expected columns must be declared explicitly in code (an allowlist), so a
  newly added column is either declared or intentionally excluded - do not infer
  silently.
- PLAUSIBILITY (substance, not just presence): add documented sanity assertions on
  declared measures so degenerate-but-non-null data also fails. At minimum:
  a constant/zero-variance measure column is a failure; and each declared measure
  must fall inside a documented plausible range (e.g. home-win rate in
  [0.45, 0.60]; per-appearance `outs_recorded` and `earned_runs` within sane
  bounds). Record the observed values so drift is visible build over build.
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
