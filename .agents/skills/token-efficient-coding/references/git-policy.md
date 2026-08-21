# Git Policy

## Local Git first

Use the local checkout for:

- current branch
- working tree status
- changed files
- local commit history and diffs
- merge-base, blame, tracked files
- recent commits and local tags

Do **not** use GitHub or remote APIs for these when local Git suffices.

## Compact commands

```bash
git status --short
git diff --stat
git diff --name-only
git diff -- path/to/file
git log -5 --oneline
git show --stat <commit>
```

## Repeated-state avoidance

Cache mentally or in task state until an operation may have changed it:

- current branch
- current HEAD
- dirty file set
- task worktree path
- base commit for the task

Refresh after: commit, merge, rebase, checkout, stash, external edits.

## Diff-first after edits

After modifications:

```bash
git diff --stat
git diff -- changed/file.py
```

Full-file reads only when surrounding context is needed beyond the diff.

## Remote APIs, GitHub CLI, MCP — remote-only

Use `gh`, REST APIs, or MCP tools for information **not** in the local checkout:

- issues and pull requests
- review comments
- remote CI / Actions status
- remote branches not fetched locally
- remote metadata and discussions

If remote content was already retrieved in the current task, reuse it.

When many remote/MCP tools are available, use only those needed for the current task. Avoid unnecessary full schema or tool-catalog expansion.

## Worktree awareness

When agents use worktrees, persist in the context pack:

```yaml
worktree:
  path: ...
  branch: ...
  base_commit: ...
  task: ...
```

Do not re-enumerate all worktrees unless required.

Orchestrator may maintain `state/worktree-state.md` when multiple parallel tasks run.

## Local vs remote decision

| Question | Tool |
|----------|------|
| What changed locally? | `git status --short`, `git diff` |
| Recent local commits? | `git log -5 --oneline` |
| PR review comments? | GitHub / MCP |
| CI failed on remote branch? | GitHub / MCP |
| File history for blame? | `git blame` / `git log -- path` |
