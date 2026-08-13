# PIPE-004 — Prediction Detail Artifacts

## Status

candidate

## Dependencies

- PIPE-003
- DATA-003

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` (`agent/PIPE-004-game-detail-artifacts`, shared with
APP-005 in the same branch since APP-005 depends directly on these artifacts)

## Goal

Persist two new committed artifacts at prediction time that today are computed
and discarded: (1) the per-game starter/bullpen/team feature breakdown already
built by FEAT-004 for scoring, and (2) a live moneyline snapshot per
bookmaker, not just DraftKings. Enables a future Streamlit detail page (see
APP-005) without giving it DuckDB access.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `scripts/daily_predictions.py`
- `src/pipelines/daily.py` (immutability contract this must not disturb)
- `src/ingestion/odds/snapshots.py`

## Allowed files

- `scripts/daily_predictions.py`
- `tests/unit/scripts/test_daily_predictions.py`

## May modify if necessary

- none

## Do not modify

- `src/pipelines/daily.py` (PIPE-001's `daily.jsonl` record contract and
  `JsonLinesPredictionStore` are untouched — new artifacts are additive
  siblings, not a schema change to the immutable prediction record)
- `src/ingestion/odds/snapshots.py` (already multi-book capable; no changes
  needed)
- `odds_snapshots_for_schedule` behavior (still filters to the canonical
  `--bookmaker`, still drives `market_probability`/`edge`/`daily.jsonl`
  exactly as before)

## Inputs

- `today_matrix["rows"][i]["features"]` from `build_feature_matrix` (already
  computed in `main()`, previously only logged as a row count)
- The Odds API response, now fetched without a `bookmakers` filter so every
  US book for the region comes back (`fetch_odds_payload(..., bookmakers=None)`)

## Outputs

- `state/predictions/game_features.jsonl` — one line per
  `(run_date, game_pk, build_id)`: `{run_date, game_pk, build_id,
  prediction_timestamp, features}`
- `state/predictions/odds_books.jsonl` — one line per
  `(run_date, game_pk, bookmaker, snapshot_timestamp)`: `{run_date, game_pk,
  bookmaker, home_american, away_american, snapshot_timestamp, source}`.
  `snapshot_timestamp` is part of the key (not just `bookmaker`) so a
  same-day re-run with line movement appends a new observation instead of
  raising a spurious immutability conflict against a price that has since
  moved — `game_features.jsonl` correctly omits it since its features don't
  change intraday for a locked `build_id`.

## Requirements

- `fetch_odds_payload` call in `main()` no longer restricts the live fetch to
  one bookmaker; `odds_snapshots_for_schedule` (canonical DraftKings record)
  is unchanged and still receives the same payload.
- New `all_book_snapshots_for_schedule(payload, schedule)` keeps every book
  instead of filtering to one, keyed by `(game_pk, bookmaker)`, latest
  snapshot timestamp wins per book — mirrors `odds_snapshots_for_schedule`'s
  matching/latest-wins logic but is display/comparison-only, never used to
  build the immutable prediction record.
- New `append_jsonl_records(path, records, key_fields=...)` — append-only,
  identical re-write for an existing key is a no-op, conflicting re-write
  raises `ValueError`. Used for both new artifacts; does not reuse or modify
  `JsonLinesPredictionStore`.

## Critical correctness constraints

- `daily.jsonl` / `market_probability` / `edge` computation is byte-for-byte
  unchanged — the model's actual prediction stays pinned to the canonical
  bookmaker (DraftKings by default) regardless of how many books are fetched.
- New artifacts are append-only; `game_features.jsonl` is idempotent by
  `(run_date, game_pk, build_id)` (a genuine conflict there means a locked
  build's features changed, which should raise). `odds_books.jsonl` includes
  `snapshot_timestamp` in its key so it accumulates a time series of book
  observations rather than raising on ordinary intraday line movement.

## Acceptance criteria

- Re-running the pipeline for an already-predicted date does not duplicate or
  mutate existing `game_features.jsonl` lines, and appends (rather than
  conflicts on) fresh `odds_books.jsonl` observations when prices moved.
- A payload with multiple bookmakers produces one `odds_books.jsonl` row per
  book per game per snapshot; the canonical `daily.jsonl` record is
  unaffected by books other than the configured `--bookmaker`.
- Existing odds-matching/placeholder/pipeline tests continue to pass
  unmodified.

## Required tests

- unit (`tests/unit/scripts/test_daily_predictions.py`): multi-book grouping
  keeps every book, latest-timestamp-wins per book, `append_jsonl_records`
  idempotency + conflict detection.

## Handoff

- Added `all_book_snapshots_for_schedule` (keyed by `(game_pk, bookmaker)`,
  display-only) alongside the existing single-book
  `odds_snapshots_for_schedule`, which is unchanged.
- Added generic `append_jsonl_records` append-only/idempotent/conflict-checked
  writer (mirrors `JsonLinesPredictionStore`'s contract without touching that
  class) and wired it to two new artifacts: `state/predictions/game_features.jsonl`
  (captures the per-game feature breakdown `today_matrix` already computed
  but previously discarded, keyed by `(run_date, game_pk, build_id)`) and
  `state/predictions/odds_books.jsonl` (multi-book comparison snapshots,
  keyed by `(run_date, game_pk, bookmaker, snapshot_timestamp)` so intraday
  line movement across re-runs doesn't trip the conflict check).
  Both are configurable via new `--features-output` / `--odds-books-output`
  flags, defaulting alongside `--output`.
  `fetch_odds_payload` is now called with `bookmakers=None` so the live fetch
  returns every US book; the canonical DraftKings-only prediction path
  (`odds_snapshots_for_schedule`, `daily.jsonl`) is untouched.
- Commands run: `python -m pytest tests/unit/scripts/test_daily_predictions.py
  tests/unit/ingestion/odds tests/integration/pipelines/test_daily_pipeline.py -q`
  — 36 passed.
- Built in an isolated worktree (`agent/PIPE-004-game-detail-artifacts`) after
  a first attempt directly on `main` was clobbered by a concurrent in-place
  `git checkout` from another orchestrator's task; no functional changes
  resulted from that, just a redo.
- Known limitations: no live end-to-end run against the real Odds API in this
  session (offline unit coverage only); operator should confirm real payload
  shape includes the expected book count for a real slate on first live run.
- No ADR change required. No `state/CURRENT.md` update yet — pending review
  gate per repo convention.
