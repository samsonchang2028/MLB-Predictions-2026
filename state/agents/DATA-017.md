# DATA-017 Agent Status

- Task ID: DATA-017
- Active role: Implementer (repair pass complete)
- Status: READY_FOR_REVIEW
- Branch: `agent/DATA-017-column-coverage`
- Worktree: `C:\Users\sfkim\OneDrive\Desktop\sideproj\predictions-1-wt-DATA-017`
- Current activity: repair-loop fixes committed. 101 validation tests (unit +
  integration) and 443 non-XGBoost repository tests pass.
- Repair pass (this run) fixed:
  - `tests/unit/validation/test_coverage.py` was missing `import pytest`
    despite using `@pytest.mark.parametrize`, so the whole module failed to
    collect (`NameError: name 'pytest' is not defined`). Added the import.
  - Verified (no change needed): all 4 `INSERT INTO silver.games` call sites
    in that file were already consistent with the new 5-column
    `(game_pk, game_type, abstract_game_state, detailed_state, game_date)`
    schema.
  - After the import fix, `tests/integration/validation/test_semantic_completeness.py::
    test_clean_fixture_semantic_dimension_passes_and_records_coverage` failed:
    it asserted every validity dimension is PASS on the shared DATA-006
    fixture. That fixture deliberately includes a spring-training appearance
    (game_pk 400, `game_type='S'`) to exercise the documented
    `pitching.non_regular_season` advisory WARN (see
    `test_dataset_validation.py`). The test only passed before this repair
    because of the bug this repair fixes (`_semantic_completeness` /
    `_validity_dimensions` collapsing WARN into PASS). Root-caused, not
    special-cased: updated the assertion to expect `structural == WARN`
    (advisory, non-blocking, names `pitching.non_regular_season`) while
    `semantic_completeness == PASS`, `temporal_leakage == PASS`, and overall
    `artifact["status"] == PASS` (advisory WARN never blocks). No fixture,
    threshold, or check was weakened.
- Repair contents (already correct pre-repair, now committed):
  - `constant_is_degenerate` flag on `MeasureColumn`: a required stat constant
    at any single value (zero or nonzero) across >= `DEGENERATE_MIN_SAMPLE`
    populated rows is FAIL, not just constant-zero.
  - Shared `COMPLETED_GAME_PREDICATE` in `src/validation/checks.py` (excludes
    Postponed/Suspended/Cancelled `detailed_state` even when
    `abstract_game_state = 'Final'`, per DATA-012), reused in
    `coverage.py`'s team-outcome population and home-win-rate queries so
    those lifecycle rows don't skew coverage.
  - `_dimension_status` helper in `certification.py`: FAIL > WARN > PASS,
    replacing the old `FAIL if any FAIL else PASS` that silently dropped WARN
    at the dimension level.
- Commands run:
  - `python -m pytest tests/unit/validation/ tests/integration/validation/ -q`
    -> 101 passed.
  - `python -m pytest -q --ignore=tests/unit/evaluation/test_runner.py
    --ignore=tests/unit/experiments/test_comparison.py
    --ignore=tests/unit/experiments/test_expanding.py
    --ignore=tests/unit/experiments/test_rolling.py
    --ignore=tests/unit/models/test_xgboost.py` -> 443 passed, no regressions.
- Blocking issue: none. (xgboost-import-blocked modules above are a
  pre-existing environment gap, not a DATA-017 defect, and were excluded per
  the orchestrator's instructions.)
- Committed: yes, on `agent/DATA-017-column-coverage`. Not merged to main.

## Reviewer + Tester gate — both PASS

**Reviewer verdict: APPROVE.** No P0/P1. Verified (not just trusted) that a
100%-NULL required column and a required family with zero usable inputs both
FAIL (not WARN); the generalized constant-value degeneracy check only fires
above `DEGENERATE_MIN_SAMPLE`; the `_dimension_status` WARN-propagation fix is
used at both call sites; the `COMPLETED_GAME_PREDICATE` extraction is a
byte-identical pure refactor (no weakened check); measure-column allowlists are
hardcoded, not schema-inferred; all 8 thresholds are documented with
baseball-specific reasoning; and the DATA-006-fixture WARN-assertion fix is
legitimate (game_pk 400 is genuinely spring training). Re-ran tests: 101 +
443 passed. Two non-blocking P3s: `HOME_WIN_RATE_MIN_SAMPLE`/
`FAMILY_EMPTY_MIN_GAMES` are independently-chosen similar-purpose scale gates
(could use a one-line note on why they differ); `_measure_group_check`
duplicates some branch logic already in `_column_status` for message wording.

**Tester verdict: LOOKS_SAFE_TO_MERGE.** Re-ran both suites (105/447 after
additions). Probed: zero-row table vs. all-NULL column (column-level check
PASSes on an empty table by design, but the family-level check correctly FAILs
once population scale is met — documented tradeoff, not a gap); constant-zero
vs. NULL conflation (safe, both require `non_null > 0`); `DEGENERATE_MIN_SAMPLE`
boundary (correct, no off-by-one); NULL `detailed_state` on a completed game
(correctly passes through as completed); `detailed_state` containing-but-not-
prefixed-with "Postponed" (hypothetical, doesn't match any real MLB
`detailedState` value seen anywhere in the codebase); two simultaneous
distinct-family failures (both reported, not just the first). Found and closed
one real test-coverage gap: no existing test drove a **semantic** (as opposed
to structural) WARN through the certification-layer aggregation functions
repaired in this task — added 4 regression tests in
`tests/unit/validation/test_certification.py` (committed `b02dac3`,
test-only), confirmed they fail against the pre-repair `_dimension_status`
logic and pass against the candidate.

**Orchestrator decision: MERGE.** No P0/P1 from either gate; all P2/P3s are
non-blocking and recorded here. Merged to `main`.
