---
name: token-efficient-coding
description: >-
  Reduces LLM token use from repeated repository discovery, verbose shell output,
  redundant Git/GitHub calls, and duplicate agent context. Applies progressive
  discovery, bounded tool output, local Git first, persistent repo maps, and
  task context packs. Use when writing, reviewing, testing, planning, exploring
  repositories, using shell/Git/GitHub tools, delegating coding tasks, or
  operating in multi-agent workflows. Harness-agnostic: Cursor, Claude Code,
  Codex, Copilot, or any coding agent with skills support. Correctness, security,
  and leakage checks always override token savings.
metadata:
  standard: agentskills
  harness-agnostic: true
---

# Token-Efficient Coding

Harness-agnostic policy for any coding agent (orchestrator, implementer, reviewer, tester, or general-purpose). Works with local shell, file-read tools, Git, GitHub CLI, `gh`, and remote APIs/MCP where available.

Install globally across harnesses: see [INSTALL.md](INSTALL.md).

Central principle: **do not rediscover information the system already knows.**

Token efficiency is **subordinate** to correctness, test quality, architecture validation, security review, and temporal/data-leakage detection.

## Priority order

1. Reuse supplied context.
2. Read persistent repo indexes before exploring implementation.
3. Read explicitly referenced files before searching.
4. Search exact symbols or paths before scanning directories.
5. Bound shell and tool output.
6. Prefer local Git for facts already in the checkout.
7. Avoid rereading unchanged context.
8. Pass compact context packs between agents.
9. Expand discovery only when evidence requires it.
10. Persist useful structural discoveries for future agents.
11. Never compromise correctness to save tokens.

## Progressive discovery (summary)

Do not jump to broad exploration. Escalate only when a concrete correctness question remains unanswered.

| Level | Source |
|-------|--------|
| 0 | Task, context pack, `state/repo-map.md`, `state/CURRENT.md`, supplied architecture |
| 1 | Files listed in task or context pack |
| 2 | Targeted `rg`/grep for symbols, paths, errors, imports |
| 3 | Immediate directory neighborhood, callers, adjacent tests |
| 4 | Broad repo discovery — **justify with a specific unresolved question** |

Valid reasons to broaden: unknown dependencies, security-sensitive behavior, interface callers, temporal/data leakage, architectural invariants, unclear failures, cross-package impact.

Details: [references/discovery-policy.md](references/discovery-policy.md)

## Before any discovery command

Ask internally:

- What exact question am I trying to answer?
- What is the smallest tool call that answers it?
- Do I already have this information?

Do not run exploratory commands merely to become generally familiar with the repository.

## Shell, Git, remote APIs (summary)

- **Shell / terminal**: stat/name-only before full diff; targeted paths; concise test runners; `head`/`tail` for large output. See [references/shell-policy.md](references/shell-policy.md).
- **Git (local)**: `git status --short`, `git diff --stat`, `git diff -- path`, `git log -5 --oneline`. Refresh cached branch/HEAD/dirty state only after operations that change them. See [references/git-policy.md](references/git-policy.md).
- **GitHub / remote / MCP**: issues, PRs, CI, remote-only metadata — not for local status/diff/history. See [references/git-policy.md](references/git-policy.md).

Map harness tools to the same intent: bounded file reads = read only needed paths/ranges; bounded search = scoped symbol/path search.

## Persistent repository context

Repositories adopting this system maintain concise indexes under `state/`:

- `repo-map.md` — where modules, tests, docs, tasks live
- `task-context/<TASK-ID>.md` — lead agent–prepared context pack per task
- optional: `architecture-index.md`, `dependency-notes.md`, `worktree-state.md` when useful

Do not copy source code into these files. Update when structure materially changes.

Context pack schema and role duties: [references/context-pack.md](references/context-pack.md)

## Multi-agent flow

```text
User → Lead/Orchestrator → Context Pack → Implementer → Reviewer → Tester
```

Downstream agents **start from the context pack**. Append only material discoveries; do not independently rebuild full repo context from zero. Same applies when a single agent runs multiple phases in one session — reuse prior context instead of rediscovering.

## Role quick reference

### Orchestrator / lead agent

Context compiler, not bare task dispatcher. Before delegation: read task, `state/repo-map.md`, identify affected files, ADRs, invariants, tests; build compact context pack; delegate with read-first lists. Update persistent context when structure changes.

### Implementer

Start from context pack + task. Read listed files. Targeted search when needed. Smallest clean change. Targeted tests first; broaden by risk. Report material discoveries to context pack.

### Reviewer

Start from task, context pack, changed files, targeted diffs, invariants. `git diff --stat` then per-file diffs. Broaden for correctness, security, leakage, interfaces — never approve to save tokens.

### Tester

Start from context pack. Tests for changed behavior first; concise output; expand by risk. Adversarial tests where appropriate.

### General coding agent (no formal roles)

Apply the same discovery order: supplied context → repo map → listed files → targeted search → neighborhood → broad exploration only if needed.

## Prevent repeated reads

Avoid rereading unchanged files in the same task unless modified, incomplete prior read, or merge/rebase. After edits, prefer `git diff -- path` or a partial read over full-file reread when sufficient.

## Persistent knowledge updates

Persist durable structural facts (new package locations, renamed subsystems, test locations, dependency boundaries). Do **not** persist debugging noise, full diffs, or one-off errors.

## Correctness escape hatch

If a constrained approach leaves a **material correctness question unanswered**, broaden discovery until resolved. Soft discovery budgets in context packs are escalation signals, not barriers.

## Measurement

When usage analyzers are available (CodeBurn, harness dashboards, etc.), compare trends across sessions: input tokens per task, shell calls, file reads, Git/remote calls, repeated reads, tool-output volume, context reuse between agents. Do not optimize from a single session.

## Reference files

Load only when the current step needs detail:

- [references/discovery-policy.md](references/discovery-policy.md) — levels, search patterns, reread policy, examples
- [references/shell-policy.md](references/shell-policy.md) — output bounding, test invocation, staged disclosure
- [references/git-policy.md](references/git-policy.md) — local vs remote, compact commands, worktrees
- [references/context-pack.md](references/context-pack.md) — schema, orchestrator duties, examples
