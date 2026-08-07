# META-001 — Agent and Harness Foundation

## Status

candidate

## Dependencies

None.

## Execution

Primary role: implementer  
Review required: yes  
Tester required: no  
Worktree required: no

## Goal

Install the vendor-neutral repository instructions, role definitions, task graph, state handoff, and ADR structure.

## Read first

- `AGENTS.md`
- `docs/project_execution_contract.md`
- `docs/roadmap.md`

## Allowed files

- `AGENTS.md`
- `agents/`
- `docs/`
- `tasks/`
- `state/`

## Do not modify

- application source code
- model code
- data pipelines

## Acceptance criteria

- all referenced files exist,
- links/paths are internally consistent,
- no vendor-specific folder contains unique project truth,
- `state/CURRENT.md` identifies DATA-001 as the next implementation task.

## Required tests

Documentation/path sanity only.

## Handoff

Summarize installed execution contract and any path changes.
