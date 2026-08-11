# DATA-018 Agent Status

- Task ID: DATA-018
- Active role: Implementer (dispatched)
- Status: IMPLEMENTING
- Branch: `agent/DATA-018-hollow-invalidation`
- Worktree: `C:\Users\sfkim\OneDrive\Desktop\sideproj\predictions-1-wt-DATA-018`
- Current activity: filed after discovering that a naive re-run of the
  game-detail backfill (post-DATA-016 projection fix) would fetch 0 games and
  silently leave the hollow 2021-2025 build in place. Adds an invalidate
  capability + operator script chaining invalidate -> refetch -> re-normalize
  -> re-certify. Does not itself launch the live 4.5h re-ingest.
- Blocking issue: None.
- Note: Disjoint surface from OBS-001/APP-001 (both touch unrelated modules).
