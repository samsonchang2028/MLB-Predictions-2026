# PIPE-005 Agent Status

- Task ID: PIPE-005
- Active role: **Orchestrator** (awaiting merge approval)
- Status: **CANDIDATE — gates passed, uncommitted**
- Branch: `agent/PIPE-005-pregame-detail-refresh`
- Worktree: main checkout (`predictions-1`)
- Blocking issue: none

## Problem

Daily operator reused stale `silver.pitcher_starters` because it never re-fetched
MLB `probablePitchers` for today's Preview/Live games.

## Solution (implemented)

Before predictions, for today's Preview/Live regular-season slate:

1. `invalidate_game_detail_payloads`
2. `backfill_game_details(..., retry_unresolved=True)`
3. `normalize_silver`

Optional `--skip-detail-refresh` for offline replay.

## Gate status

| Gate | Result |
|---|---|
| Implementer | done |
| Reviewer | **APPROVE** (P3 notes only) |
| Tester | **PASS** — 19 + 17 unit tests |

## Files changed (implementer)

- `scripts/daily_predictions.py`
- `tests/unit/scripts/test_daily_predictions.py`
- `tasks/PIPE-005-pregame-detail-refresh.md`

## Also in working tree (not PIPE-005 scope)

Uncommitted starter-skip gate + board UI from prior session:
`src/app/board.py`, `src/app/daily_board_page.py`, `tests/unit/app/test_board.py`

Orchestrator should either merge separately or combine into one PR with user approval.

## Next step

User/Orchestrator: commit + merge when ready, then re-run:

```powershell
python scripts\daily_predictions.py --date 2026-08-13
```

LAD @ MIL should pick up Dodgers probable after MLB refresh.

## Commands to verify post-merge

```powershell
python -m pytest tests/unit/scripts/test_daily_predictions.py tests/unit/app/test_board.py -q
```
