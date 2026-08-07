# Project Changelog

This file records concise durable project milestones, not every code edit.

## Unreleased

### Added

- Vendor-neutral agent execution contract.
- Orchestrator, Implementer, Reviewer, and Tester role definitions.
- V1 task graph.
- Project-state handoff format.
- Initial architecture decision records.

### Decisions

- Repository files are the source of truth across coding harnesses.
- Worktree lifecycle belongs to the orchestration layer.
- Temporal leakage checks are merge-blocking.
