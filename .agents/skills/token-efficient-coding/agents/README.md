# Harness adapters (optional)

Core policy lives in `SKILL.md` and `references/`. Files here are **optional per-harness metadata** — not required for correctness.

| File | Harness | Purpose |
|------|---------|---------|
| `openai.yaml` | OpenAI Codex | UI display name and implicit-invocation policy |

Cursor, Claude Code, and other Agent Skills runtimes load `SKILL.md` frontmatter only and ignore `openai.yaml`.

Do not duplicate behavioral policy into adapter files.
