# Discovery Policy

## Progressive levels

### Level 0 — Supplied context

Use without further exploration when sufficient:

- Task description and acceptance criteria
- Task context pack (`state/task-context/<TASK-ID>.md`)
- `state/repo-map.md`, `state/CURRENT.md`
- Explicitly supplied architecture or ADR summaries in the dispatch

### Level 1 — Targeted reads

Read only files referenced by the task or context pack (`read_first`, `allowed files`, listed ADRs).

### Level 2 — Targeted search

Search for **specific** symbols, filenames, modules, tests, imports, config keys, or error strings:

```bash
rg "ExactSymbol" src tests
rg --files src/market
rg -l "game_pk" src/features
```

### Level 3 — Local neighborhood

Inspect the immediately relevant directory, adjacent module, caller, dependency, or matching test file.

### Level 4 — Broad exploration

Repository-wide scans only when Levels 0–3 cannot answer a question **necessary for correctness**. State the unresolved question before broadening.

## Default avoid list

Unless a specific question requires them:

```text
find .
tree
ls -R
recursive directory dumps
repository-wide read/cat
broad grep without a target
reading every ADR / task / test / source file
repeated git status / git log for unchanged state
```

## Prefer

```bash
rg "ExactSymbol" src tests
rg --files src/market
git status --short
git diff --stat
git diff -- path/to/file.py
git log -5 --oneline
```

## Reread policy

Do not reread unchanged files in the same task unless:

- a tool or another agent modified the file
- merge/rebase may have changed it
- the prior read was incomplete
- a specific section must be revisited

Do not repeatedly reread `AGENTS.md`, repo maps, ADRs, or task docs in an unchanged session.

After edits, prefer:

```bash
git diff -- path/to/file.py
```

over a full-file read when the diff plus minimal surrounding context suffices.

## Repository map usage

Read `state/repo-map.md` once at task start (or trust the context pack if it already incorporated it). Use it to choose Level 1–2 targets instead of directory enumeration.

Update the map when you discover durable structural changes (new package, renamed module, new canonical test location).

## Escalation examples

| Situation | Action |
|-----------|--------|
| Unknown import target | `rg` symbol → read defining module |
| Changing public function | `rg` callers across repo |
| Rolling feature change | read leakage tests + feature module |
| Security-sensitive path | read auth/validation code even if not listed |
| Failure source unclear | broaden trace until root cause identified |

## Efficient vs wasteful

**Wasteful**

```bash
ls
ls src
ls src/models
grep -R calculate_edge .
```

**Better**

```bash
rg "calculate_edge" src tests
```

**Wasteful** (goal: identify changed files only)

```bash
git status
git diff
git log
```

**Better**

```bash
git status --short
git diff --name-only
```

## Token-conservation theater

One huge `cat` is not better than several precise reads. Optimize **total relevant context**, **repeated context**, **tool-output volume**, and **duplicate discovery** — not raw command count.
