# DATA-012 - Postponed-Final Score Validation Fix

## Status

done (merged)

## Dependencies

- DATA-006 (validation checks)

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Origin

Discovered by the DATA-011 real-path smoke test against live MLB data
(2024-04-10). Game `game_pk` 747139 had `abstractGameState='Final'` but
`detailedState='Postponed'` with NULL scores. The DATA-006 `results.valid_scores`
check flagged any abstract-Final game with a missing score, so it would
spuriously FAIL certification for every postponed game across 2021-2025. The
DATA-006 fixture only covered postponed games with abstract state `Preview`, so
this quirk slipped through.

## Change

`check_results_scores` (`src/validation/checks.py`) now excludes games whose
`detailed_state` is Postponed/Suspended/Cancelled (COALESCE for NULL) while still
flagging genuinely completed games with a missing/negative score. Check id
(`results.valid_scores`) and severity (P1) unchanged.

## Allowed files

- `src/validation/checks.py`
- `tests/unit/validation/`

## Acceptance criteria

- A postponed/suspended/cancelled game reported with `abstractGameState='Final'`
  is not flagged.
- A genuinely completed game with a missing/negative score is still flagged.

## Required tests

- Regression: postponed-abstract-Final game not flagged; completed missing-score
  game still flagged; negative score still flagged.

## Merge-blocking conditions

- Any weakening of missing/negative-score detection for genuinely completed games.

## Verification

Reviewer APPROVED, Tester PASS (219). Real-path smoke re-run (2024-04-10) after
the fix certified PASS with no merge-blocking findings and 14/14 odds MATCHED.
