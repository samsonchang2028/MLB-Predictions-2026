# Shell Policy

Treat shell output as costly context. Use staged disclosure.

## Output bounding

### Git diffs

```text
git diff --stat
    ↓
git diff --name-only
    ↓
git diff -- relevant/file.py
```

Not `git diff` for the whole tree unless genuinely required.

### Avoid by default

```bash
git diff          # full tree
git log           # unbounded
pytest -vv        # verbose passing lines
find .
tree
grep -R pattern .
```

### Prefer

```bash
git status --short
git diff --stat
git diff -- path/to/relevant_file.py
git log -5 --oneline
rg "symbol" src tests
pytest path/to/test.py -q --tb=short
```

## Tests

Start targeted:

```bash
pytest tests/unit/market/test_market_engine.py -q --tb=short
```

On failure, surface:

- failing test names
- concise traceback
- relevant assertion messages

Not thousands of passing test lines.

Expand scope when change risk warrants (integration, leakage, regression suites).

## Search

Prefer `rg` with path scope:

```bash
rg "FunctionName" src tests
rg -n "error string" src
rg --files src/features
```

Not repository-wide unscoped grep.

## Large output

When output may be huge:

1. Target a path or file first
2. Filter (`rg`, `--name-only`, `--stat`)
3. Summarize (`head`, `tail`, counts)
4. Capture to file and inspect relevant range if needed

Do not hide information required to diagnose failures.

## One precise command

Prefer one command that answers the question over a chain of exploratory commands.

**Bad** (only need changed files)

```bash
git status && git diff && git log -10
```

**Good**

```bash
git diff --name-only
```

**Bad** (edited one helper)

```bash
pytest
```

**Good**

```bash
pytest tests/unit/features/test_starter.py -q --tb=short
```

## Before large output

Ask:

- Can I get filenames first?
- Can I get a count or stat first?
- Can I search for an exact symbol?
- Can I inspect only failing cases or changed lines?
