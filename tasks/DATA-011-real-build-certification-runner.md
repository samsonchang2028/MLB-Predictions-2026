# DATA-011 - Real 2021-2025 Build + Certification Runner

## Status

done (merged) — runner in-repo; real multi-hour build is operator-run

## Dependencies

- DATA-005 (MLB game-detail backfill machinery)
- DATA-006 (validation checks)
- DATA-007 (certification artifact layer)
- DATA-008 (historical odds archive ingestion)
- DATA-009 (odds mapping/coverage)

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Provide the runnable glue that produces a real 2021-2025 historical MLB dataset
build from the MLB Stats API wrapper, ingests the published historical odds
archive, runs validation + odds mapping, and emits a durable PASS/FAIL
certification artifact. This is the operational step that gates FEAT-002/FEAT-003.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `src/ingestion/mlb/schedule.py`, `src/ingestion/mlb/game_detail.py`
- `src/ingestion/odds/historical.py`
- `src/transforms/silver.py`
- `src/validation/__init__.py` (certify_and_write, validate_odds_archive)

## External inputs (provided by operator)

- MLB Stats API wrapper: `MLB-StatsAPI` (toddrob99), imported as `statsapi`
  (public, unauthenticated). Satisfies ADR-004 "MLB StatsAPI wrapper pattern".
- Historical odds archive file `mlb_odds_dataset.json` whose SHA-256 equals the
  ADR-004 published hash `3f952fd0...b882b` (verified present at repo root).

## Allowed files

- `src/ingestion/mlb/statsapi_fetchers.py` (new: wrapper -> exact-bytes adapters)
- `src/pipelines/` (new: `certify_historical.py` runner)
- `tests/unit/ingestion/mlb/` (fetcher-adapter tests)
- `tests/unit/pipelines/`, `tests/integration/pipelines/` (runner wiring tests)
- `pyproject.toml` (add `MLB-StatsAPI` dependency)

## Requirements

- The statsapi adapters must produce deterministic payload bytes for a given
  wrapper response (canonical JSON: sorted keys, compact separators) so that
  restarts are idempotent and DATA-005 hash-skip works. The wrapper response is
  the retained "raw API response" under ADR-004's wrapper pattern.
- Schedule adapter: `fetch_schedule(request) -> bytes` calling
  `statsapi.get("schedule", request)`.
- Game-detail adapter: `fetch_game_detail(endpoint, request) -> bytes | None`
  calling `statsapi.get("game", {"gamePk": <pk>, **request})`, returning None on
  an upstream not-found and re-raising other transport errors so DATA-010
  isolation records a retryable failure.
- Politeness: bounded request rate and basic retry/backoff, without masking hard
  failures.
- The runner must be idempotent and restartable (reuse a stable run_id) and must
  not silently drop postponed/doubleheader/cancelled games.
- The runner sequences: schedule ingest (2021-2025) -> game-detail backfill ->
  `normalize_silver` -> historical odds archive ingest -> DATA-009 mapping +
  coverage -> `certify_and_write`. It prints/returns the certification status and
  artifact path.
- No 2026 data is ingested for the certified development build (2026 remains the
  untouched holdout per ADR-003/ADR-004).

## Correctness constraints

- Raw immutability + idempotency (DATA-001/DATA-005) preserved.
- `game_pk` canonical throughout.
- Certification FAILs on any P0/P1 or leakage failure (DATA-007 contract).
- The committed certification artifact is the durable gate FEAT-002/003 read.

## Acceptance criteria

- Adapters unit-tested with a stubbed `statsapi` (no network): canonical-bytes
  determinism, None-on-not-found, error propagation.
- Runner wiring integration-tested end-to-end with injected fixture fetchers +
  the existing small odds fixture (no network), producing a PASS artifact.
- Real execution is operator-run (network + 80MB archive) and produces a
  committed `state/data-certifications/certification-<STATUS>-*.json`.

## Required tests

- Unit: schedule + game-detail adapter serialization/None/error behavior against
  a fake statsapi module.
- Integration: full runner against injected fixtures yields a PASS certification
  artifact and non-empty coverage report; a seeded leakage/P0 failure yields FAIL.

## Merge-blocking conditions

- Any adapter that returns non-deterministic bytes for identical wrapper output.
- Any silent acceptance of a not-found as a valid payload.
- Any 2026 data entering the certified development build.
- Certification able to PASS with a P0/P1 or leakage failure.

## Handoff

Record the adapter contract, the runner CLI/entry usage, exact commands, test
results, and the real-run instructions (install `MLB-StatsAPI`, ensure the
archive file present, run the pipeline, commit the artifact).
