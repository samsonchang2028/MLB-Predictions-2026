# META-001 — Agent and Harness Foundation

## Status

done

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

## Completion handoff

- Added repository-level Git policy for task branches, commits, review, testing, and Orchestrator-owned merges.
- Changed files: `AGENTS.md`, this task file, `tasks/index.md`, and `state/CURRENT.md`.
- Validation: documentation/path sanity and `git diff --check` passed.
- Reviewer: approved with no open P0/P1 findings after one repair loop.
- Tester: not required for this documentation-only task.
- Known limitations: downstream task-metadata completeness and legacy `old/` documentation are deferred P2 follow-ups.
- ADR/state: no ADR required; project state updated to unlock DATA-001.
