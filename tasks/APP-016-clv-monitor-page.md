# APP-016 — CLV Monitor Streamlit Page

## Status

ready

## Dependencies

- MARKET-008 (`build_clv_report`, `load_clv_population`)
- MARKET-009 (`odds_closes.jsonl`, live capture on homelab)
- APP-015 (Streamlit multipage pattern)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/APP-016-clv-monitor`

## Goal

Add a Streamlit sidebar page that **automatically** surfaces CLV metrics from
live artifacts (`daily.jsonl`, `journal.jsonl`, `odds_books.jsonl`,
`odds_closes.jsonl`) — strict N, mean CLV, verdict, capture health, recent
strict-close games. Read-only; not PLAY policy.

## Read first

- `state/task-context/APP-016.md`
- `src/market/clv_evaluation.py` (`build_clv_report`)
- `scripts/market_clv_study.py`
- `src/app/market_edge_page.py` (page pattern)
- `src/app/parlay_builder_page.py` (how-to expander pattern)

## Allowed files

- `src/app/clv_monitor.py`
- `src/app/clv_monitor_page.py`
- `pages/9_CLV_Monitor.py`
- `tests/unit/app/test_clv_monitor.py`
- `tasks/APP-016-clv-monitor-page.md`
- `README.md` (one sidebar row)

## Do not modify

- `src/market/clv_evaluation.py` logic (unless P0 bug blocking display)
- PLAY policy / ADR-007 acceptance
- Prediction pipeline

## Requirements

1. **Page:** `pages/9_CLV_Monitor.py` + `clv_monitor_page.py`
2. **Metrics:** strict N, mean/median CLV, positive CLV rate, strict ROI, verdict
3. **Capture health:** odds_closes total, live vs backfill counts, latest snapshot
4. **Anchor toggle:** latest (default) vs earliest in sidebar
5. **Auto-refresh:** `st.cache_data(ttl=300)` + manual refresh button
6. **How-to expander:** explain CLV, strict window, anchor difference
7. **Disclaimer:** observational only; ADR-007 not production
8. **Table:** recent strict-close games with CLV, close source, result
9. **Pure helpers** in `clv_monitor.py` with unit tests

## Acceptance criteria

- Page loads via `streamlit run streamlit_app.py` → CLV Monitor
- Uses existing `build_clv_report` / `load_clv_population`
- Tests pass: `PYTHONPATH=src pytest tests/unit/app/test_clv_monitor.py -q`
- No changes to PLAY threshold or model code

## Handoff

Summary, files, test commands, reviewer/tester follow-ups.
