# MARKET-008 — Closing Line Value Validation Before PLAY Policy Promotion

## Status

done (candidate — merge pending orchestrator)

## Dependencies

- MARKET-001 (no-vig / edge engine)
- MARKET-006 (policy evaluation population + slices patterns)
- OBS-001 / OBS-002 (prediction journal)
- ML-015 (prospective diagnostic evidence)
- PIPE-004 (`odds_books.jsonl` multi-book snapshots)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/MARKET-008-clv-validation`

## Goal

Before changing PLAY/staking policy, validate whether model-market edge predicts
favorable movement against the closing market. Evidence and observability only —
not model retraining, not betting automation, not production PLAY changes.

## Read first

- `AGENTS.md`, `state/CURRENT.md`, `state/task-context/MARKET-008.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `docs/decisions/ADR-007-play-staking-policy.md` (PROPOSED — do not accept)
- `tasks/MARKET-006-play-policy-study.md`, `tasks/ML-015-prospective-model-market-diagnostic.md`
- `docs/research/ml-015-prospective-model-market-diagnostic.md`
- `reports/experiments/ml-015-prospective-diagnostic.json`
- `reports/market/play-policy-study-market-006.json`
- `src/market/play_policy.py`, `src/market/policy_evaluation.py`, `src/market/engine.py`
- `src/app/board.py`, `scripts/market_policy_study.py`
- `scripts/daily_predictions.py` (odds_books upsert semantics)
- `state/predictions/daily.jsonl`, `journal.jsonl`, `odds_books.jsonl`

## Allowed files

- `src/market/clv.py` (new — pure CLV functions)
- `src/market/clv_evaluation.py` (new — population, slices, report)
- `scripts/market_clv_study.py` (new — CLI)
- `tests/unit/market/test_clv.py` (new)
- `reports/market/` (generated via CLI only)
- `src/app/clv_display.py`, `src/app/market_edge_page.py` (optional dashboard — only if straightforward)
- `tasks/MARKET-009-prospective-closing-odds-capture.md` (follow-up task if CLV data insufficient)
- `state/task-context/MARKET-008.md`, `state/agents/MARKET-008.md`
- `tasks/index.md`, `state/CURRENT.md` (state updates on completion)

## Do not modify

- `src/models/`, `src/features/`, `src/pipelines/daily.py`
- ADR-006, ADR-007 acceptance status
- `DEFAULT_EDGE_THRESHOLD` in `src/app/board.py`
- `state/predictions/*.jsonl` (immutable — read only)
- production PLAY logic or staking/Kelly/bankroll
- live external API calls from dashboard handlers

## Core CLV semantics

- `edge = model_probability - market_probability` (home-relative)
- `edge_selected_side`: edge >= 0 → HOME, else AWAY
- Selected-side CLV: `closing_market_p_selected - prediction_market_p_selected`
- Keep separate: `raw_model_favorite`, `market_favorite`, `edge_selected_side`

## Closing-line definition (document exactly)

Primary (strict):

> Latest `odds_books.jsonl` no-vig snapshot for the prediction source bookmaker
> on `(game_pk, run_date)` where `prediction_timestamp < snapshot_timestamp <= game_start_timestamp`.

Secondary (pregame latest — report separately):

> Latest snapshot for source book where `snapshot_timestamp <= game_start_timestamp`
> (may equal prediction-time line → CLV = 0).

If insufficient strict-close coverage, verdict **CLV DATA INSUFFICIENT** and file MARKET-009.

## Orchestrator preflight (2026-09-09)

Artifact probe on live `state/predictions/`:

- ~309 unique games (latest prediction per `game_pk`)
- ~316 DraftKings `odds_books` rows per slate
- **Strict valid CLV window (close after pred, before first pitch): N ≈ 8**
- ~129 games: close snapshot after first pitch (reject)
- ~171 games: close snapshot ≤ prediction time (no movement / same batch)
- Kalshi close rows: 0 in current artifacts

Expect **Case 4 — insufficient CLV data** unless implementer finds another in-repo source.

## Phases

1. Data requirements audit (document available vs missing fields)
2. Canonical CLV population (latest pred per game_pk, exact joins, deduped)
3. Selected-side CLV computation + integrity rules
4. Aggregate metrics (CLV N vs outcome N separated)
5. Required slices: edge buckets, crossover, agreement, home/away, fav/dog, lead time, book
6. Interpretation verdict (Cases 1–4) + explicit ADR-007 recommendation
7. CLI + JSON/Markdown reports
8. Optional dashboard observability (non-blocking)
9. Focused tests (side conversion, timestamps, dedup, buckets, missing data, ROI separation)
10. MARKET-009 follow-up task if CLV data insufficient

## CLI

```bash
python scripts/market_clv_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --odds-books state/predictions/odds_books.jsonl \
  --run-id market-008
```

Exit non-zero on integrity failures. Never mutate source artifacts.

## Acceptance criteria

- CLV computable from artifacts OR explicit CLV DATA UNAVAILABLE report
- Closing-line definition documented in report
- Deterministic deduplicated population
- HOME/AWAY selected-side transforms tested
- Aggregate + slice tables in JSON and Markdown
- Separate CLV and outcome denominators
- Explicit ADR-007 recommendation (do not accept ADR-007)
- No production PLAY changes
- Tests pass; `git diff --check` clean

## Handoff

(Implementer completes.)
