# APP-001 — Streamlit Daily Board

## Status

blocked

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
