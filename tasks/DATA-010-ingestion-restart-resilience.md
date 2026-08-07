# DATA-010 - MLB Backfill Restart Resilience

## Status

done (merged a87ef2b)

## Dependencies

- DATA-005

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Goal

Fix two backfill-runtime resilience bugs in the DATA-005 MLB game-detail backfill
that DATA-006 validation surfaced but could not fix (validation must not modify
ingestion source).

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-001-storage.md`
- `src/ingestion/mlb/game_detail.py`

## Allowed files

- `src/ingestion/mlb/`
- `tests/unit/ingestion/mlb/`
- `tests/integration/ingestion/mlb/`

## Bugs to fix

1. **Reused fixed `run_id` on restart raises a PK violation.**
   `_record_attempt` inserts into `mlb_game_detail_attempts` keyed by
   `(ingestion_run_id, game_pk)`. Re-recording a `missing`/`failed` attempt for a
   game under the same `run_id` on restart raises a PK conflict. The success path
   already uses `ON CONFLICT DO NOTHING`; `_record_attempt` does not. Make attempt
   recording restart-safe (upsert/`ON CONFLICT` with correct last-attempt
   semantics) or require+document unique run_id — decide and test the chosen
   contract.

2. **Backfill abort granularity.** `_verify_raw_payload` raising `RuntimeError`
   aborts the entire remaining backfill on the first tampered/missing raw file,
   blocking progress on unrelated `game_pk`s. Make integrity failure isolate to
   the offending `game_pk` (record it failed/retryable) and continue, without
   weakening the fail-loud guarantee that a tampered payload is never silently
   accepted.

## Acceptance criteria

- Restart reusing the same `run_id` does not raise and records correct
  attempt state.
- A single tampered/missing raw payload marks that `game_pk` failed/retryable and
  does not abort unrelated games; integrity is still enforced (no silent accept).

## Required tests

- Reused-`run_id` restart regression test.
- Single-bad-payload isolation test (one bad game_pk, others still processed,
  bad one marked retryable).

## Merge-blocking conditions

- Any silent acceptance of a tampered/hash-mismatched raw payload.
- Any regression to the DATA-005 immutability/idempotency guarantees.

## Handoff

Record the run_id contract chosen, isolation behavior, commands/tests run, and
any interaction with DATA-006 Bronze-integrity checks.
