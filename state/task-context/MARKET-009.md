# MARKET-009 Context Pack — Prospective Closing Odds Capture

## Mission

Capture real pre-first-pitch closing snapshots so MARKET-008 strict CLV can reach
≥30 games. Do not change production PLAY policy or accept ADR-007.

## Root cause (MARKET-008 finding)

- `odds_books.jsonl` upserts **one latest row** per `(run_date, game_pk, bookmaker)`
- Most snapshots are at prediction batch time or after first pitch
- **Strict CLV N=8** on latest-per-game + odds_books fallback (Case 4)

## Preflight backfill analysis (orchestrator, 2026-09-09)

| Anchor | Strict close source | Approx N |
|--------|---------------------|----------|
| **latest** pred + odds_books | 8 | insufficient |
| **earliest** pred + odds_books | **112** | retrospective backfill candidate |

Earliest-anchor backfill uses real odds_books timestamps but changes the
prediction population vs board default — document explicitly if used to reach 30+.

## Deliverables

1. **`state/predictions/odds_closes.jsonl`** — append-only closes keyed by
   `(run_date, game_pk, bookmaker, prediction_timestamp)` with `capture_method`
2. **`scripts/closing_odds_capture.py`** — live capture before first pitch
   (mirror `scripts/kalshi_pregame_capture.py` pattern; The Odds API; DraftKings
   + optional all books)
3. **`src/market/odds_closes.py`** — pure helpers: validate window, backfill, index
4. **Update `src/market/clv_evaluation.py`** + **`scripts/market_clv_study.py`**
   — prefer `odds_closes.jsonl`; optional `--prediction-anchor latest|earliest`
5. **`--backfill-from-odds-books`** on capture script for retrospective populate
6. **Tests** in `tests/unit/market/test_odds_closes.py`; update `test_clv.py`
7. **Optional:** `deploy/systemd/mlb-predictions-closing-odds.timer` (10–15 min)
8. Run backfill + live capture (if API key + due games) + rerun CLV study
   targeting **strict N ≥ 30**

## Reuse

- `scripts/daily_predictions.py`: `fetch_odds_payload`, `all_book_snapshots_for_schedule`, `append_jsonl_records`, schedule loading
- `scripts/kalshi_pregame_capture.py`: due-window, already-captured, failure isolation
- `src/market/clv.py`: `strict_close_valid`
- `src/market/clv_evaluation.py`: population + report (extend, don't rewrite)

## Integrity rules

- `prediction_timestamp < snapshot_timestamp <= game_start_timestamp`
- Never mutate `daily.jsonl` / `journal.jsonl`
- One close row per key; conflicting re-write → error (append-only)
- CLV still separates CLV N from outcome N

## Acceptance commands

```bash
python scripts/closing_odds_capture.py --backfill-from-odds-books --anchor earliest
python scripts/closing_odds_capture.py --date $(date +%F)   # if due games + API key
python scripts/market_clv_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --odds-books state/predictions/odds_books.jsonl \
  --odds-closes state/predictions/odds_closes.jsonl \
  --prediction-anchor earliest \
  --run-id market-009-rerun
PYTHONPATH=src pytest tests/unit/market/test_odds_closes.py tests/unit/market/test_clv.py -q
git diff --check
```

## Success gate for user request

- MARKET-009 implemented and capturing
- MARKET-008 rerun with **strict CLV N ≥ 30** (document anchor if not latest)
