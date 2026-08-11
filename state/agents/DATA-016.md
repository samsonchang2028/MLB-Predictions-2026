# DATA-016 Agent Status

- Task ID: DATA-016
- Active role: none (gates passed, merged)
- Status: **APPROVED / MERGED to main**
- Branch: `agent/DATA-016-pitching-stats` (merged)
- Worktree: `C:\Users\sfkim\OneDrive\Desktop\sideproj\predictions-1-wt-DATA-016`
  (pending Orchestrator cleanup)
- Current activity: Merged. Full 2021-2025 re-ingest is the next gate, owned by the
  Orchestrator/operator (long-running, single-writer, not launched automatically).
- Verification:
  - focused ingestion/normalization suite: 56 passed
  - real Bronze -> Silver smoke: 5 completed games on five dates/seasons
    (2021-2025), 40 appearances, all ten pitching measurements 100% populated
  - starter/reliever identity: 10 starters and 30 relievers
- Blocking issue: Full 2021-2025 re-ingest intentionally not launched; the
  Orchestrator owns the long-run/single-writer gate.
- Note: Data-integrity hardening after the first real experiment exposed 100%-NULL
  pitching stats. Parallel with the other two hardening tasks (disjoint surfaces).

## Repair loop 2 (this pass)

Reviewer + Tester found 2 failures on the loop-1 candidate and the Tester added a
third constraint (`numberOfPitches` required; completed games with a missing/empty
boxscore must fail ingestion, not persist hollow). A repair worker had already
implemented the fix in `src/ingestion/mlb/game_detail.py` +
`tests/unit/ingestion/mlb/test_game_detail.py` (uncommitted) but left 4 integration
tests failing.

Root cause of the 4 integration failures: `tests/integration/ingestion/mlb/
test_game_detail_backfill.py::_detail_payload` always returned a hollow boxscore
(`pitchers: []`), and `_schedule_game` mapped every non-Final `detail` to the same
`codedGameState="S"`. The new completed-game hollow guard correctly rejected the
hollow payload for `Final` games in these fixtures.

Fix (test-fixture only, no further production-code change needed):
- `_detail_payload(game_pk, *, played=True)` now returns a real, valid pitching
  line (all required stats incl. `numberOfPitches`, valid `inningsPitched` text)
  for played/completed games; `played=False` keeps the original hollow boxscore
  for legitimately non-played games.
- `_schedule_game` now maps `detail` to the correct MLB coded game state
  (`Suspended`->`U`, `Postponed`->`D`, `Cancelled`->`C`, `Rescheduled`/other->`S`,
  `Final`->`F`) instead of a uniform `S`, so `_is_completed_lifecycle` exempts
  non-played games on the real code rather than by accident.
- Call sites in `test_restart_skips_terminal_rows_and_retry_resolves_missing_and_failed_games`
  pass `played=` explicitly per game_pk (only 1001 is Final); all other tests'
  games default to Final and use the new default (`played=True`).

Commands run:
```
python -m pytest tests/unit/ingestion/mlb/test_game_detail.py tests/integration/ingestion/mlb/ -q
# 48 passed
python -m pytest -q --ignore=tests/unit/evaluation/test_runner.py \
  --ignore=tests/unit/experiments/test_comparison.py \
  --ignore=tests/unit/experiments/test_expanding.py \
  --ignore=tests/unit/experiments/test_rolling.py \
  --ignore=tests/unit/models/test_xgboost.py
# 437 passed (full repo suite except pre-existing xgboost-import-blocked modules,
# unrelated to this task -- this worktree's venv lacks the xgboost package)
```

Committed: `0408dff` "DATA-016: tighten completed-game hollow guard; require
numberOfPitches" on `agent/DATA-016-pitching-stats`.

Live network smoke (`scripts/data016_pitching_smoke.py`) was not re-run this pass
(no production code changed, only test fixtures); the prior loop-1 live smoke
result above still stands as the network-verified evidence.

Handoff: ready for reviewer + tester re-gate. No known open issues in the
completed-game guard or the fixed fixtures.

## Reviewer + Tester re-gate (repair loop 2) — both PASS

**Reviewer verdict: APPROVE.** No P0/P1. Re-ran the required tests (48 passed),
traced every caller of `_validate_payload`/`_assert_pitching_stats_present`/
`_game_lifecycle`, cross-checked lifecycle classification against `schedule.py`'s
existing `_LIFECYCLE_RANK` table (exact match), confirmed idempotency/DATA-010
semantics unaffected, confirmed no leakage, confirmed the integration-fixture fix
is legitimate (not a weakened test — the non-played hollow-boxscore path is still
exercised for games 1002/1004/1005). Findings (both non-blocking, deferred):
- P2: 5 new validation branches (duplicate/invalid pitcher id, person-id
  mismatch, team-identity mismatch, non-dict `players`) have no direct unit test
  yet, though the logic reads correctly. Addressed in the same pass — see below.
- P3: redundant `_game_lifecycle` lookup runs even for already-fetched games
  (harmless PK lookup, discarded).
- P3: `_is_completed_lifecycle(None)` defaults strict/completed; only reachable
  from tests, documented in the docstring.

**Tester verdict: LOOKS_SAFE_TO_MERGE.** Re-ran both required suites (58 after
additions / 437 full repo) and confirmed the xgboost-import-blocked-modules gap
reproduces identically on `main` (pre-existing, unrelated). Added 5 adversarial
regression tests (partial-hollow payload, `inningsPitched` format matrix,
cross-side duplicate pitcher id, non-completed-lifecycle-with-malformed-data,
idempotent re-run) — committed as `11f9f0d` (test-only). All pass. One
informational P3: duplicate pitcher id is checked per-side, not across
home/away — unreachable with real MLB data (globally unique person ids), so
left as documented current behavior rather than tightened.

**Orchestrator decision: MERGE.** No P0/P1 from either gate; all P2/P3s are
non-blocking and recorded here for the historical record. Merged to `main`.
