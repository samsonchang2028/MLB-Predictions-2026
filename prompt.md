Orchestrator takeover

You are taking over this MLB moneyline predictor repo from another harness. The repository is the source of truth — do not rely on prior chat history.

Act as Orchestrator (agents/orchestrator.md). Preserve the workflow: Orchestrator → Implementer → Reviewer + Tester → fix → gate → merge. Do not commit worker changes directly to main.

Read first, in order:

1. AGENTS.md
2. agents/orchestrator.md
3. state/CURRENT.md
4. docs/roadmap.md
5. tasks/index.md
6. Accepted ADRs under docs/decisions/
7. The task file(s) for anything marked ready, implementing, candidate, or changes_requested

Before doing anything, inspect and report:
• current branch / worktrees / active task branches
• latest commits and unmerged work
• task statuses vs state/CURRENT.md and tasks/index.md
• open reviewer/tester findings
• test status

Then continue from actual repo state:
• do not restart completed work
• do not discard unmerged candidate work
• maintain temporal-ML leakage rules and 2026 as final holdout
• use one worktree per PR-sized task when needed
