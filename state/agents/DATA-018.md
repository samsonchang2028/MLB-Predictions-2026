# DATA-018 Agent Status

- Task ID: DATA-018
- Active role: Implementer (complete, pending review/test gates)
- Status: IMPLEMENTED
- Branch: `agent/DATA-018-hollow-invalidation`
- Worktree: `C:\Users\sfkim\OneDrive\Desktop\sideproj\predictions-1-wt-DATA-018`

## Summary

Added `invalidate_game_detail_payloads(storage_root, game_pks) -> int` to
`src/ingestion/mlb/game_detail.py`: deletes the matching
`bronze.mlb_game_detail_payloads` rows only (transactional select-count +
delete), never touches `raw/` or `bronze.mlb_game_detail_attempts`, and raises
on empty/non-positive/duplicate `game_pks` via a new local
`_distinct_positive_ints` (stricter than the existing `_positive_ints`, which
silently dedups). Exported from `ingestion.mlb.__init__`.

Combined with the pre-existing `retry_unresolved=True` flag on
`backfill_game_details`, this makes the game-detail re-ingest for the certified
2021-2025 build actually re-fetch instead of a silent no-op.

Added `scripts/data018_reingest.py`: the operator entry point for the real
multi-hour live re-ingest. Selects all 2021-2025 `game_pk`s from
`bronze.mlb_games`, invalidates only the ones not already re-fetched under the
given `--run-id` (so a restart with the same `--run-id` never re-invalidates
already-good re-fetched data), re-backfills in batches (default 250, prints
progress per batch), re-normalizes Silver, and re-certifies. Not run against
the real network/database by this task -- that is a separate, later,
explicitly-authorized step.

## Files changed

- `src/ingestion/mlb/game_detail.py` -- `invalidate_game_detail_payloads` +
  `_distinct_positive_ints`.
- `src/ingestion/mlb/__init__.py` -- export `invalidate_game_detail_payloads`.
- `scripts/data018_reingest.py` -- new operator script.
- `tests/integration/ingestion/mlb/test_game_detail_invalidate.py` -- new
  unit/integration/regression tests.

## Tests run

- `python -m pytest tests/unit/ingestion/mlb/ tests/integration/ingestion/mlb/ -q`
  -> 101 passed (93 pre-existing + 8 new: 1 targeting/isolation, 5
  invalid-input parametrized cases, 1 invalidate->re-fetch->re-store round
  trip, 1 regression proving un-invalidated games still no-op skip).
- `python -m pytest -q` (full repo suite, this worktree has xgboost) -> 553
  passed (main was 545; +8 matches the new tests, zero regressions).
- Manual smoke of `scripts/data018_reingest.py` with an injected fake fetcher
  (no network, temp storage root, monkeypatched
  `statsapi_fetchers.make_game_detail_fetcher`): seeded 3 hollow payloads,
  ran the script end-to-end (invalidate -> batched backfill -> normalize ->
  certify), confirmed all 3 came back with real (non-hollow) pitching stats
  under the new run_id, then re-ran with the SAME run_id and confirmed it
  invalidated 0 / re-fetched 0 (idempotent restart, all skipped via the
  existing hash-verify skip path). The certification artifact this produced
  (expected FAIL -- a 3-game fixture has no odds archive / real coverage) was
  deleted afterward and is not committed; this was a manual check, not a
  pytest test, per the task's "do NOT invoke it as a side effect of any test."

## Confirmation: raw-payload immutability preserved

No code path added by this task ever writes to, deletes from, or otherwise
touches any file under `raw/`. `invalidate_game_detail_payloads` only issues
`DELETE FROM bronze.mlb_game_detail_payloads`. The integration test
`test_invalidate_removes_only_targeted_rows_and_preserves_everything_else`
explicitly asserts all raw files (including the two invalidated game_pks')
are byte-identical after invalidation, and
`test_invalidate_then_retry_unresolved_genuinely_refetches_hollow_game`
asserts the original hollow raw file is still present and byte-identical on
disk after the re-fetch stores a new, differently-hashed raw file alongside
it (delete-then-insert of the pointer row, never update-in-place; two raw
files now exist, the old hollow one and the new real one, both immutable).

## Known limitations

- The invalidate function does not verify `game_pks` exist in
  `bronze.mlb_games` (unlike `_target_game_pks`); a nonexistent `game_pk`
  simply matches/deletes 0 rows. Not required by the task; the operator
  script only ever passes `game_pk`s it just read from `bronze.mlb_games`.
- Per-game progress inside `backfill_game_details` itself is still coarse
  (only per-call totals); the operator script's batching (default 250) is
  what makes multi-hour progress visible, not a change to the core module.

## Blocking issue

None. Ready for reviewer + tester (task requires both).
