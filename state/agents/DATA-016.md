# DATA-016 Agent Status

- Task ID: DATA-016
- Active role: Implementer (repair loop 2 complete)
- Status: CANDIDATE (implementation + focused/live smoke gates passed; repair
  loop 2 fixed reviewer/tester findings, full suite green)
- Branch: `agent/DATA-016-pitching-stats`
- Worktree: `C:\Users\sfkim\OneDrive\Desktop\sideproj\predictions-1-wt-DATA-016`
- Current activity: Awaiting required reviewer/tester re-gate and Orchestrator
  decision on the full 2021-2025 backfill.
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
