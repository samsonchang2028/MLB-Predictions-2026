# MARKET-009 — Prospective Closing Odds Capture

## Status

ready

## Dependencies

- MARKET-008 (CLV validation — Case 4 insufficient data)
- PIPE-004 (`odds_books.jsonl` multi-book snapshot persistence)
- PIPE-003 / PIPE-002 (daily operator)
- DATA-003 (live timestamped odds ingestion)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/MARKET-009-closing-odds-capture`

## Goal

Enable strict closing-line value (CLV) evaluation on prospective predictions by
capturing a bookmaker closing snapshot **after prediction time and at or before
first pitch**. MARKET-008 found only **8 strict-close games** (below the
30-game minimum) because current `odds_books.jsonl` rows are mostly captured at
prediction batch time or after first pitch.

## Evidence from MARKET-008 (market-008 run)

On current `state/predictions/` artifacts (2026-09-09):

- 309 latest-per-game predictions
- **Strict CLV N: 8** (pred < close <= start)
- 171 games: close snapshot ≤ prediction time (same batch — CLV = 0)
- 129 games: close snapshot after first pitch (rejected)
- 1 game: missing DraftKings row
- Verdict: **CLV DATA INSUFFICIENT** (Case 4)

## Read first

- `tasks/MARKET-008-clv-validation.md`
- `reports/market/clv-study-market-008.json`
- `state/task-context/MARKET-008.md`
- `scripts/daily_predictions.py` (odds_books upsert semantics)
- `scripts/kalshi_pregame_capture.py` (pregame window capture pattern)
- `docs/decisions/ADR-004-opening-benchmark-methodology.md`

## Requirements

1. Capture at least one timestamped moneyline snapshot per `(run_date, game_pk,
   bookmaker)` in the strict CLV window: `prediction_timestamp <
   snapshot_timestamp <= game_start_timestamp`.
2. Preserve immutability: append/upsert with explicit timestamps; never overwrite
   historical snapshots silently.
3. Integrate with the daily operator so closing capture runs automatically
   before first pitch for same-day slates.
4. Do **not** use historical archive closing lines for live prospective CLV
   without explicit timestamp semantics (ADR-004 opening-benchmark only).
5. Re-run MARKET-008 CLV study after sufficient capture; target ≥30 strict-close
   games before CLV can gate PLAY promotion.

## Acceptance criteria

- Closing snapshots captured in the strict CLV window for production slates
- `odds_books.jsonl` (or successor artifact) supports strict-close joins
- MARKET-008 CLV study strict N ≥ 30 on a subsequent run, or documented blocker
- No production PLAY policy changes
- Tests cover timestamp window validation and idempotent capture

## Do not modify

- Production PLAY threshold or ADR-007 acceptance status
- `state/predictions/*.jsonl` immutability policy for historical rows

## Handoff

(Implementer completes.)
