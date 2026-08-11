# DATA-018 — Invalidate Hollow Game-Detail Payloads So Re-Ingest Actually Re-Fetches

## Status

ready

## Dependencies

- DATA-016
- DATA-010

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Give the Orchestrator/operator a safe, tested way to make the full 2021-2025
game-detail re-ingest (blocked on DATA-016's projection fix) actually re-fetch
data, instead of silently doing nothing.

## Problem

`backfill_game_details` (`src/ingestion/mlb/game_detail.py`) treats a `game_pk`
as done the moment `bronze.mlb_game_detail_payloads` has a row for it:

```python
existing = connection.execute(
    """SELECT payload_sha256, raw_path
       FROM bronze.mlb_game_detail_payloads WHERE game_pk = ?""",
    [game_pk],
).fetchone()
if existing is not None:
    ...  # only re-verifies the stored file's hash, never re-fetches
    result["skipped_fetched"] += 1
    continue
```

`bronze.mlb_game_detail_payloads` has `game_pk BIGINT PRIMARY KEY` and the
insert is `ON CONFLICT DO NOTHING`. All 14,520 games from the certified
2021-2025 build already have a payload row — they succeeded, they're just
hollow (DATA-016). There is currently no `--force`/invalidate path anywhere in
`game_detail.py` or `pipelines/certify_historical.py`. Simply re-running the
certification pipeline with the DATA-016-fixed projection would fetch **0**
games and silently leave the hollow data in place while reporting success.

Even after removing the payload row, `prior_status` (read from
`bronze.mlb_game_detail_attempts`, `PRIMARY KEY (ingestion_run_id, game_pk)`)
would still show `'fetched'` from the original run, so a re-run also needs
`retry_unresolved=True` — that flag already exists and is already tested; it
is the *invalidation* step that is missing.

## Goal (restated)

Add a minimal, tested function to remove the stale pointer row for a set of
`game_pk`s from `bronze.mlb_game_detail_payloads` so they become eligible for
a genuine re-fetch, without violating raw-payload immutability, plus an
operator entry point that chains invalidate -> re-fetch (`retry_unresolved`)
-> re-normalize Silver -> re-certify for the full 2021-2025 set.

## Requirements

- Add `invalidate_game_detail_payloads(storage_root, game_pks) -> int` (exact
  name/signature at implementer discretion) to `src/ingestion/mlb/game_detail.py`
  that:
  - deletes the matching rows from `bronze.mlb_game_detail_payloads` for the
    given `game_pk`s,
  - does **not** touch anything under `raw/` — the immutable content-addressed
    raw files stay on disk forever, they simply become unreferenced by any
    current payload row (a stale hollow raw file is still evidence of the
    defect and must not be deleted),
  - does **not** delete or rewrite `bronze.mlb_game_detail_attempts` rows —
    the historical audit trail ("this run fetched a payload for this game_pk
    on this date") stays intact and honest; do not fabricate a new attempt
    status,
  - raises on invalid input consistent with existing helpers in this module
    (e.g. non-positive/duplicate `game_pk`s) rather than silently no-op'ing,
  - is transactional (all-or-nothing) like `_store_success`.
- After invalidation, `backfill_game_details(..., game_pks=<same set>,
  retry_unresolved=True, run_id=<new run id>)` must genuinely re-fetch,
  re-validate (hollow-payload guard from DATA-016 applies), and re-store those
  games. Prove this with an integration test: seed a hollow payload the way
  the old build would have, invalidate it, re-run with a fetcher that returns
  a real pitching line, and assert the stored `payload_json` now contains
  real stats.
- Add a small operator script under `scripts/` (pattern-match
  `scripts/data016_pitching_smoke.py` / `pipelines/certify_historical.py`'s
  `main()`) that chains, for the real 2021-2025 build:
  1. select the target `game_pk`s (all 2021-2025 regular+non-regular season
     games currently in `bronze.mlb_games` — the whole build is hollow, this
     is not a partial-subset repair),
  2. `invalidate_game_detail_payloads`,
  3. `backfill_game_details(..., retry_unresolved=True, run_id=<new id>)`
     using the real MLB-StatsAPI fetcher (reuse
     `ingestion.mlb.statsapi_fetchers.make_game_detail_fetcher`),
  4. `normalize_silver`,
  5. `certify_and_write`.
  This script is the operator/Orchestrator entry point for the actual
  multi-hour live run — do NOT execute it as part of the automated test
  suite (network + hours), and do NOT invoke it as a side effect of any test.
  Print progress incrementally (this runs for hours; the operator needs to see
  it's alive) and be safely resumable/restartable if interrupted (DATA-010
  semantics — reusing the same new `run_id` on a restart should resume, not
  restart from scratch).
- PRESERVE IMMUTABILITY: never mutate an existing raw file's bytes; never
  overwrite a `bronze.mlb_game_detail_payloads` row in place with different
  content for the same `game_pk` (the existing `_store_success` conflict guard
  — refuse if a payload row somehow already exists with a different hash —
  must still hold after invalidation, i.e. invalidate-then-store is
  delete-then-insert, never update-in-place).
- Do NOT touch `bronze.mlb_games` (schedule) or `bronze.mlb_game_detail_attempts`
  schema/constraints.

## Critical correctness constraints

- Never use information unavailable at the prediction timestamp (not directly
  relevant here, but do not introduce anything that reads future data).
- Raw payloads remain immutable; ingestion stays idempotent (DATA-010
  semantics) for every code path this task touches or adds.
- This task does NOT itself launch the multi-hour live re-ingest. It only adds
  the tested capability + the operator script. The Orchestrator/operator
  decides when to actually run it.

## Acceptance criteria

- Unit test: `invalidate_game_detail_payloads` removes exactly the targeted
  payload rows, leaves other `game_pk`s and all `bronze.mlb_game_detail_attempts`
  rows untouched, and leaves raw files on disk untouched.
- Integration test: invalidate -> `backfill_game_details(retry_unresolved=True)`
  with a real-shaped (non-hollow) fetcher genuinely re-fetches and re-stores a
  previously-hollow game, and the new stored payload contains real pitching
  stats (contrast with the old hollow one).
- The new operator script is documented (module docstring, like
  `data016_pitching_smoke.py`) with exactly how to run it and what to check
  before/after (mirroring DATA-016's "smoke before full backfill" discipline —
  this script IS the full backfill, so the smoke check already done in
  DATA-016 is the precondition, not something to redo here).
- No existing test weakened; full existing ingestion suite still passes.

## Required tests

- unit: `invalidate_game_detail_payloads` targeting/isolation behavior.
- integration: invalidate -> re-fetch -> re-store round trip, proving hollow
  data is genuinely replaced, not silently skipped.
- regression: confirm the pre-DATA-018 no-op failure mode (re-running backfill
  over already-payload'd games with the DATA-016-fixed projection alone fetches
  nothing) is what motivated this task — one test may assert the *current*
  skip behavior for un-invalidated games is unchanged (invalidation is opt-in,
  not automatic).

## Handoff

Record:

- summary, files changed, commands run, test results, known limitations,
- explicit confirmation that no raw file was deleted/mutated by any new code
  path,
- whether the operator script was smoke-tested (should be, with an injected
  fake fetcher, not network) or only unit/integration tested,
- do NOT report the live 4.5h re-ingest as run — that is a separate, later,
  explicitly-authorized step.
