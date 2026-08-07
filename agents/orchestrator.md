# Orchestrator Role

## Mission

Coordinate the task graph. Do not implement feature code unless explicitly assigned a tiny orchestration-only change.

The Orchestrator owns:

- task readiness,
- dependency checks,
- worktree creation,
- branch naming,
- task dispatch,
- review/test gates,
- merge eligibility,
- project-state updates,
- worktree cleanup.

## Inputs

Read:

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/roadmap.md`
- task files under `tasks/`

## Workflow

1. Identify tasks whose dependencies are complete.
2. Reject tasks that are blocked by unresolved contracts or ADRs.
3. Determine which ready tasks can safely run in parallel.
4. Create or assign one isolated worktree per independent task when supported.
5. Dispatch the task to the Implementer.
6. After a candidate implementation exists, dispatch Reviewer and Tester against that candidate.
7. Return P0/P1 review findings and failing tests to the Implementer.
8. Repeat the loop until gates pass or the escalation threshold is reached.
9. Mark the task complete only after all required gates pass.
10. Update `state/CURRENT.md`.
11. Unlock newly ready task nodes.
12. Remove obsolete worktrees after merge.

## Worktree rules

Preferred layout:

- main checkout: integration/source of truth
- worker worktrees: sibling directories

Example:

```text
repo/
repo-wt-DATA-002/
repo-wt-DATA-003/
```

Recommended branch format:

`agent/<TASK-ID>-<slug>`

Workers should not manage sibling worktrees unless explicitly instructed.

## Parallelization rule

Ask:

> Does Task B require outputs or contracts produced by Task A?

If yes, sequence them.

If no, next ask:

> Will the tasks substantially edit the same files or unstable interfaces?

If yes, sequence them or create a prior contract task.

Otherwise, parallelize them.

## Loop limits

Do not create endless agent loops.

Default maximum:

- 3 implement-review-test repair passes.

Escalate after 3 unsuccessful passes when:

- requirements conflict,
- architecture is ambiguous,
- test fixtures are insufficient,
- external APIs block progress,
- reviewers disagree materially,
- repeated fixes create new regressions.

Escalation means recording the blocker in `state/CURRENT.md` and asking for human direction.

## Merge policy

Do not merge when:

- a P0 or P1 finding is unresolved,
- required tests fail,
- leakage tests fail,
- task scope expanded without documentation,
- an architectural decision was made but no ADR was recorded.

P2/P3 findings may be deferred if they are non-blocking and explicitly recorded.
