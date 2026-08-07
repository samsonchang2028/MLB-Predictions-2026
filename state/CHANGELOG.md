# Project Changelog

This file records concise durable project milestones, not every code edit.

## Unreleased

### Added

- Vendor-neutral agent execution contract.
- Orchestrator, Implementer, Reviewer, and Tester role definitions.
- V1 task graph.
- Project-state handoff format.
- Initial architecture decision records.
- Repository-level Git policy for task-prefixed candidate commits and Orchestrator-owned integration.
- Idempotent local storage foundation with immutable raw writes and DuckDB Bronze/Silver/Gold schemas.
- Immutable MLB schedule ingestion with canonical `game_pk`, preserved status history, and deterministic temporal lineage.
- Append-only timestamped moneyline ingestion with transaction safety and timezone-aware instants.
- Deterministic Silver normalization with explicit join cardinalities and commence+team odds↔`game_pk` mapping.
- Point-in-time team features with shift-before-roll, Final-pair history gate, and leakage tests.
- Historical data source strategy and certification gate planning for 2021-2025 MLB data.

### Decisions

- Repository files are the source of truth across coding harnesses.
- Worktree lifecycle belongs to the orchestration layer.
- Temporal leakage checks are merge-blocking.
- MLB Stats API remains the V1 historical baseball source, the finalized historical odds archive uses opening moneylines for benchmark evaluation, and certified historical data is required before dependent feature/model work.
