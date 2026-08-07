# MLB Moneyline Predictor — Agent Instructions

This repository uses a vendor-neutral agent workflow. The repository, not any chat transcript, is the source of truth.

## Read order

Before changing code:

1. Read this file.
2. Read `state/CURRENT.md`.
3. Read the task file you were assigned under `tasks/`.
4. Read only the project docs and ADRs listed by that task.
5. Inspect the existing implementation before proposing changes.

Do not rely on remembered context from Codex, Claude, Cursor, or another harness when repository state disagrees with it.

## Roles

This project defines four roles:

- **Orchestrator** — `agents/orchestrator.md`
- **Implementer** — `agents/implementer.md`
- **Reviewer** — `agents/reviewer.md`
- **Tester** — `agents/tester.md`

A task file defines which role acts first and whether review/testing gates are required.

## Source-of-truth hierarchy

When instructions conflict, use this order:

1. Explicit current task requirements in `tasks/<TASK-ID>.md`
2. Accepted ADRs in `docs/decisions/`
3. Project docs under `docs/`
4. This `AGENTS.md`
5. Existing implementation conventions
6. Harness/session instructions

Do not silently override a higher-priority rule. If a task requires a deliberate architectural change, add or update an ADR.

## Core engineering principles

- Prefer the smallest implementation that fully satisfies the task.
- Do not add abstractions for hypothetical future requirements.
- Do not refactor unrelated code.
- Do not create framework-like helpers for a single use.
- Prefer a simple function over a class when stateful behavior is unnecessary.
- Prefer existing dependencies over adding new ones.
- Prefer explicit readable code over clever indirection.
- Keep module boundaries clear so parallel branches are easy to merge.
- Do not modify files outside the task's allowed surface unless required for correctness.
- If additional files must change, explain why in the task handoff.
- Do not rewrite files merely for style.
- Do not add TODO scaffolding that is not required by the current task.
- Avoid speculative configuration.
- Avoid dependency injection, factories, base classes, registries, plugin systems, and generic repositories unless a concrete current requirement needs them.

## ML correctness rules

These rules are non-negotiable.

- Never use information unavailable at the prediction timestamp.
- Never allow the current game's result or statistics to influence that game's features.
- Shift before rolling for game-level and appearance-level rolling features.
- Never use future odds snapshots for historical predictions.
- Prediction timestamps must precede first pitch.
- Training, validation, calibration, and test boundaries must preserve chronology.
- Do not use random train/test splits for primary model evaluation.
- Fit scalers, imputers, encoders, calibrators, and feature selectors only on the appropriate training partition.
- Hyperparameter selection must not inspect the final holdout.
- The 2026 season is the final untouched holdout unless an accepted ADR changes that policy.
- Compare expanding and recent rolling training windows.
- Primary model-quality metrics are log loss, Brier score, and calibration.
- ROC-AUC and accuracy are secondary.
- Simulated betting ROI is secondary and must never replace probability-quality evaluation.
- Market-derived features must clearly distinguish opening/current/closing snapshots.
- Closing odds may be used for post-hoc evaluation but never as an input to a pregame prediction unless the prediction timestamp is actually at close.

## Data rules

- Raw API responses are immutable.
- Ingestion must be idempotent.
- Canonical MLB game identity is `game_pk`.
- Team/date alone is not a safe unique key because of doubleheaders and reschedules.
- Every odds observation must preserve its source and timestamp.
- Do not silently drop postponed, suspended, cancelled, or doubleheader games.
- All joins must have explicit cardinality expectations.
- Unexpected one-to-many or many-to-many joins are test failures unless documented.
- Derived datasets must be reproducible from raw inputs and code.

## Testing rules

Every behavior-changing task must add or update focused tests unless the task is documentation-only.

At minimum, consider:

- unit tests for deterministic transforms,
- integration tests across module boundaries,
- leakage tests for temporal ML behavior,
- regression tests for previously discovered bugs.

Tests must verify behavior, not implementation trivia.

Do not weaken or delete a failing test merely to make a task pass unless the test is demonstrably incorrect and the reason is documented.

## Worktree policy

The **Orchestrator owns worktree lifecycle**.

Workers should normally be told they are already in the correct isolated worktree.

Do not let worker agents recursively create arbitrary worktrees unless the current harness requires it and the task explicitly permits it.

Use one worktree per independent PR-sized task.

Recommended branch naming:

`agent/<TASK-ID>-<short-slug>`

Examples:

- `agent/DATA-002-mlb-ingestion`
- `agent/FEAT-002-starter-features`
- `agent/ML-003-xgboost`

Do not create worktrees for trivial edits.

Do not merge a task until required review and test gates pass.

## Parallelism policy

Tasks may run in parallel only when:

- all declared dependencies are complete,
- their allowed file surfaces do not materially overlap,
- they do not depend on an unstable shared contract,
- their integration point is owned by a later task.

If two tasks would both heavily edit the same module, sequence them or split ownership more cleanly.

## Completion gate

A task is complete only when all required conditions hold:

- acceptance criteria are satisfied,
- relevant tests pass,
- required reviewer has no open P0/P1 findings,
- required tester has no unexplained failing tests,
- no known temporal/data leakage remains,
- task handoff is written,
- `state/CURRENT.md` is updated when project state changes.

## Handoff format

At the end of work, report:

- task ID,
- summary of changes,
- files changed,
- commands/tests run,
- pass/fail status,
- known limitations,
- reviewer/tester follow-ups if any,
- whether an ADR or project-state update is needed.

Keep handoffs factual and concise.
