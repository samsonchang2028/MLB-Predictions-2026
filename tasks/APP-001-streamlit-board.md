# APP-001 - Streamlit Daily Board

## Status

done

## Dependencies

- PIPE-001

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Create a lightweight Streamlit dashboard for today's MLB model predictions.

## Requirements

Show at minimum:

- matchup,
- model probability,
- market/no-vig probability,
- edge,
- odds snapshot time,
- model version,
- pass/play indicator if a threshold exists.

## Constraints

Keep business/model logic outside Streamlit modules.
Do not duplicate feature or market calculations in the UI.

## Completion handoff

- Added `src/app/board.py`, `src/app/daily_board_page.py`, and focused board
  tests.
- Board reads PIPE-001 immutable prediction records and displays model
  probability, market probability, edge, odds timestamp, model version, matchup,
  and display-only play indicator.
- Reviewer approved and tester passed with one deferred P2 pinned by xfail:
  malformed/stale-schema prediction records can crash the board instead of
  skipping only the bad row.
- Merged to main; see `state/agents/APP-001.md`.
