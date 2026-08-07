# Project Execution Contract

This document defines how any coding harness can work on this repository without access to another vendor's chat history.

## Principle

The repository is the durable shared memory.

No critical project knowledge may exist only inside:

- Codex conversation history,
- Claude conversation history,
- Cursor chat history,
- local scratch notes,
- undocumented agent reasoning.

## Universal context files

All harnesses should understand these paths:

- `AGENTS.md` — global project rules.
- `agents/` — role definitions.
- `docs/` — architecture and methodology.
- `docs/decisions/` — accepted architecture decisions.
- `tasks/` — executable PR-sized work.
- `state/CURRENT.md` — current project state.
- `state/CHANGELOG.md` — concise completed-task history.

Vendor-specific config may point to these files, but must not contain unique project truth.

## Vendor-specific adapters

Optional folders such as:

```text
.codex/
.claude/
.cursor/
```

may contain invocation/configuration details.

They must not be the only location for:

- architecture decisions,
- model methodology,
- task status,
- data contracts,
- testing rules,
- leakage rules.

## Standard task lifecycle

```text
ready
  ↓
implementing
  ↓
candidate
  ├─→ reviewer
  └─→ tester
        ↓
changes_requested
        ↓
implementing
        ↓
candidate
        ↓
approved
        ↓
merged
        ↓
done
```

A task may enter `blocked` from any state.

## Required task metadata

Every PR-sized task should define:

- task ID,
- status,
- dependencies,
- primary role,
- review requirement,
- testing requirement,
- goal,
- read-first context,
- allowed file surface,
- inputs,
- outputs,
- acceptance criteria,
- handoff requirements.

## Context handoff

When a harness finishes work, durable outcomes belong in the repo:

- implementation → code,
- tests → `tests/`,
- architecture decisions → ADR,
- task completion → task file/state,
- new known limitation → `state/CURRENT.md` or task handoff,
- methodology changes → relevant docs.

Do not preserve raw chain-of-thought. Preserve decisions, evidence, commands, results, and rationale needed by the next engineer.

## Worktree ownership

The orchestration layer owns worktree lifecycle.

Worker roles operate inside an assigned checkout/worktree.

This keeps the project portable because Git, branches, commits, tests, and Markdown are the shared protocol—not a vendor-specific multi-agent feature.
