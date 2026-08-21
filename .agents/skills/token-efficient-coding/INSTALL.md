# Installing token-efficient-coding (all harnesses)

This skill follows the [Agent Skills](https://agentskills.io/) layout (`SKILL.md` + optional `references/`). It is **harness-agnostic**: same content works in Cursor, Claude Code, OpenAI Codex, VS Code Copilot, and other agents that load skills from standard paths.

## Global (user scope — all repositories)

Install one copy per harness home, or symlink a single canonical folder:

| Harness | Global path |
|---------|-------------|
| Cursor | `~/.cursor/skills/token-efficient-coding/` |
| Claude Code | `~/.claude/skills/token-efficient-coding/` |
| OpenAI Codex | `~/.agents/skills/token-efficient-coding/` or `~/.codex/skills/token-efficient-coding/` |
| Any (compat) | `~/.agents/skills/token-efficient-coding/` |

**Windows example** (PowerShell, one canonical copy + symlinks):

```powershell
$src = "$env:USERPROFILE\.cursor\skills\token-efficient-coding"
foreach ($dir in @(
  "$env:USERPROFILE\.claude\skills",
  "$env:USERPROFILE\.agents\skills",
  "$env:USERPROFILE\.codex\skills"
)) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $dest = Join-Path $dir "token-efficient-coding"
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Junction -Path $dest -Target $src
}
```

**macOS/Linux example**:

```bash
SRC="$HOME/.cursor/skills/token-efficient-coding"
for DIR in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.codex/skills"; do
  mkdir -p "$DIR"
  ln -sfn "$SRC" "$DIR/token-efficient-coding"
done
```

## Repository scope (team share)

Copy or symlink into the repo so every harness picks it up when working in that project:

| Path | Harness |
|------|---------|
| `.agents/skills/token-efficient-coding/` | Codex, Cursor (compat), Agent Skills standard |
| `.cursor/skills/token-efficient-coding/` | Cursor |
| `.claude/skills/token-efficient-coding/` | Claude Code |

Prefer **one** repo path (usually `.agents/skills/`) and symlink the others if your team uses multiple harnesses.

## Harness-specific files

`agents/openai.yaml` is optional Codex UI metadata. Other harnesses ignore it. See `agents/README.md`.

## Invocation

- Explicit: `/token-efficient-coding` or attach `@token-efficient-coding` where the harness supports skills.
- Implicit: agents should apply this policy when writing, reviewing, testing, exploring repos, or using shell/Git tools (see `SKILL.md` description).
