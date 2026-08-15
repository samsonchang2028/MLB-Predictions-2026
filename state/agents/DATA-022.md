# DATA-022 Agent Status

- Task ID: DATA-022
- Active role: none — merged
- Status: **DONE** (merged to `main` at `ecbc308`, worktree/branch removed)
- Branch: `agent/DATA-022-kalshi-integration`
- Worktree: `predictions-1-wt-kalshi-integration-planning`
- Blocking issue: none

## Problem

Kalshi's MLB per-game yes/no event-contract prices are not ingested anywhere
in this repo. `docs/researcha/kalshi-integration.md` researched the shape;
this task builds the actual Bronze ingestion.

## Gate status

| Gate | Result |
|---|---|
| Implementer | done |
| Reviewer | **APPROVE** (1 non-blocking P2: `_price()` silently rounds >4-decimal Kalshi prices instead of raising; xfail-pinned) |
| Tester | **PASS** (763 passed, 1 xfailed) |

## Next step

Done. Unlocks DATA-023.
