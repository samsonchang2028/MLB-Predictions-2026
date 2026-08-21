# Task Context Packs

Orchestrator-prepared compact context for Implementer, Reviewer, and Tester.

## Location

```text
state/task-context/<TASK-ID>.md
```

## Schema

See the harness-agnostic `token-efficient-coding` skill (`references/context-pack.md` in the skill folder) for the full schema: `objective`, `read_first`, `references`, `invariants`, `expected_tests`, `likely_symbols`, `avoid`, `known_state`, `worktree`, `discovery_guidance`.

## Usage

1. Orchestrator creates the pack before dispatch.
2. Downstream agents start from the pack; expand discovery only when correctness requires it.
3. Append material discoveries (new callers, extra tests, leakage risks) in concise form.
4. Archive or delete packs when tasks merge; active observability remains in `state/agents/<TASK-ID>.md`.

## Related state

- `state/repo-map.md` — repository layout index
- `state/CURRENT.md` — project milestone and completion log
- `tasks/<TASK-ID>-*.md` — authoritative task requirements
