# DATA-015 - home_win_derivation Non-Regular-Season / Tie Handling

## Status

done (merged)

## Dependencies

- DATA-006 (validation checks)

## Origin

Surfaced by the DATA-011 real full 2021-2025 build. Certification FAILed on the
P0 check `results.home_win_derivation` with 185 flagged Final games. Diagnosis
against the built dataset:

- ALL 185 are `game_type = 'S'` (spring training).
- 184 are legitimate TIES: equal home/away score, no winner flag
  (`is_winner` null/false on both sides). `check_home_win_derivation` assumes
  every Final game has exactly one winner and unequal scores, so it flags ties
  (`hs == as_`, `hw == aw`) as inconsistent.
- 1 (`game_pk` 642061) is a decisive spring game (4-7) with no `is_winner` flag
  set on either side.
- Regular-season games (`game_type = 'R'`) are 100% consistent (zero ties).

ADR-004 scopes V1 to the 2021-2025 REGULAR seasons; spring/exhibition games are
already treated as advisory/excluded (DATA-005 P3c, DATA-006
`pitching.non_regular_season`). Certification must not FAIL on legitimate
non-regular-season tie/incomplete-winner outcomes.

## Requirements

- Certification must not FAIL on legitimate non-regular-season outcomes (spring
  ties, spring games without a populated winner flag).
- Preferred approach: restrict `check_home_win_derivation` to regular-season
  games (`game_type = 'R'`), consistent with the V1 regular-season scope. The
  implementer may additionally treat a genuine tie (equal scores, no winner) as
  a valid no-winner outcome, but MUST NOT weaken regular-season validation.
- Regular-season strictness preserved: a regular-season Final game whose winner
  flag disagrees with the scores, that declares two winners, or that declares a
  winner on equal scores, must STILL be flagged (P0).
- Decision and rationale documented in code.
- Consider (but only if trivially consistent) aligning `results.valid_scores`
  scope; note that valid_scores currently PASSes on the full build, so the sole
  required fix is `home_win_derivation`. Do not expand scope unnecessarily.

## Allowed files

- `src/validation/checks.py`
- `tests/unit/validation/`
- `tests/integration/validation/`

## Required tests

- A spring-training (`game_type='S'`) tie (equal score, no winner) does NOT fail
  `home_win_derivation`.
- A spring-training decisive game with no winner flag (like 642061) does NOT
  fail (if scoping to regular season) — assert the chosen policy explicitly.
- A regular-season (`game_type='R'`) Final game with an inconsistent winner flag
  (winner disagrees with score, or two winners) STILL fails.
- A regular-season decisive game with a correct winner PASSes.

## Merge-blocking conditions

- Any regular-season home-win inconsistency slipping through undetected.
- Certification FAILing on legitimate non-regular-season ties.
- Any weakening of the P0 severity for genuine regular-season inconsistencies.

## Handoff

Record the chosen policy (regular-season scope and/or tie handling), rationale,
tests added, commands run, and results. Note that after merge the Orchestrator
re-runs `certify` on the cached `data/` build to confirm PASS.
