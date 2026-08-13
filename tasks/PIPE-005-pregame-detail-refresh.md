# PIPE-005 — pregame game-detail refresh for daily operator

## Status

`implemented` (awaiting review/tester)

## Dependencies

- PIPE-002 (local daily operator)
- PIPE-003 (live odds + starter handling)
- DATA-005 / DATA-018 (game-detail backfill + invalidation primitives)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `no` (use branch `agent/PIPE-005-pregame-detail-refresh`)

## Goal

Before the daily operator builds inference features or writes predictions, refresh
MLB game-detail payloads for today's Preview/Live regular-season slate so
`silver.pitcher_starters.probable_pitcher_id` reflects the latest MLB
`gameData.probablePitchers` announcement.

Re-running `scripts/daily_predictions.py` must no longer reuse stale pregame
payloads that omit newly announced starters (e.g. LAD @ MIL missing home probable).

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/PIPE-002-local-daily-operator.md`
- `tasks/PIPE-003-live-daily-operator-hardening.md`
- `src/ingestion/mlb/game_detail.py` (`invalidate_game_detail_payloads`,
  `backfill_game_details`, `retry_unresolved`)
- `src/transforms/silver.py` (`normalize_silver`)
- `scripts/daily_predictions.py`
- `scripts/ingest_holdout_2026.py` (reference pattern for backfill + normalize)

## Allowed files

- `scripts/daily_predictions.py`
- `tests/unit/scripts/test_daily_predictions.py`
- `tasks/PIPE-005-pregame-detail-refresh.md`

## May modify if necessary

- `src/ingestion/mlb/__init__.py` (export only if needed for cleaner imports)

## Do not modify

- `src/pipelines/daily.py` core prediction contract
- `src/features/*` feature math
- certification / historical ingestion scripts
- unrelated Streamlit pages

## Inputs

- Today's slate from `silver.games` where `official_date = run_date`,
  `game_type = 'R'`, `abstract_game_state IN ('Preview', 'Live')`
- MLB Stats API live feed via existing `make_game_detail_fetcher`
- Local storage root / DuckDB at `data/`

## Outputs

- Daily operator step that, for today's Preview/Live slate only:
  1. invalidates existing bronze game-detail payload rows for those `game_pk`s
  2. re-fetches with `backfill_game_details(..., game_pks=..., retry_unresolved=True)`
  3. runs `normalize_silver(connection)` so `silver.pitcher_starters` updates
- Structured console logging (`[detail-refresh] fetched=... normalized=...`)
- Focused unit tests with injected fetcher (no network)

## Requirements

- Refresh runs **before** `load_prediction_inputs` / feature build / predictions.
- Scope is **only** today's Preview/Live regular-season games for the requested
  `--date` (do not invalidate/re-fetch completed Final historical games).
- Use existing primitives; do not add a parallel MLB client.
- `invalidate_game_detail_payloads` + `retry_unresolved=True` is required
  (documented DATA-018 behavior: deletion alone does not re-fetch).
- Full `normalize_silver` rebuild is acceptable for V1 (existing pattern in
  `ingest_holdout_2026.py`); do not build a partial Silver updater unless the
  full rebuild is provably too slow in tests.
- Inject fetcher in tests; production `main()` uses `make_game_detail_fetcher`.
- Optional `--skip-detail-refresh` flag for offline replay/tests that already
  have fixtures loaded (default: refresh enabled when not skipped).
- Do not weaken PIPE-005's companion starter-announcement gate if present in the
  working tree (games still missing both probables after refresh remain skipped).

## Critical correctness constraints

- Raw payloads remain immutable on disk; only bronze pointer rows are invalidated.
- Do not use post-game boxscore data as pregame features (ADR-002).
- Prediction timestamp guards in PIPE-001 remain unchanged.
- Do not mutate certified 2021-2025 historical rows beyond the normal Silver
  rebuild-from-bronze contract already used elsewhere.

## Acceptance criteria

- Re-running `daily_predictions.py` after MLB posts a new probable starter updates
  `silver.pitcher_starters` for that `game_pk` without manual invalidation scripts.
- Preview/Live slate games with both probables announced proceed to prediction;
  games still missing a probable remain skipped (if starter gate exists locally).
- Unit tests prove: invalidate called, backfill called with `retry_unresolved=True`,
  normalize called, and Final games are not targeted.
- No new dependencies.

## Required tests

- unit: refresh helper called with today's Preview/Live `game_pks` only
- unit: `retry_unresolved=True` passed to backfill
- unit: `--skip-detail-refresh` bypasses network/backfill path
- regression: existing `test_daily_predictions.py` cases still pass

## Handoff

Record summary, files changed, commands/tests run, and whether operator smoke on
a real day is still operator-run (not required in CI).
