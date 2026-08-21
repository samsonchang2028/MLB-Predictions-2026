# APP-013 — Streamlit observability dashboards + model signal front page

## Status

ready

## Dependencies

- APP-001 (daily board loaders)
- APP-002 (performance / holdout loaders)
- APP-006 (homepage artifacts)
- OBS-002 (journal enrichment)

## Execution

Primary role: **implementer**

Review required: yes

Tester required: yes

Worktree required: yes — branch `agent/APP-013-streamlit-observability`

---

## Goal

Two related Streamlit product changes (single PR):

### A. Separate analytics pages

Split model quality, market edge, betting results, and prospective evaluation so PLAY win rate is not presented as model-quality evidence.

Pages:

1. Daily Predictions (existing — keep slate view)
2. Model Quality — historical holdout + prospective metrics (separate labels)
3. Market Edge — disagreement stats, no profitability implied
4. Betting Results — PLAY W/L, ROI (artifact-backed)
5. Prospective Evaluation — frozen production monitoring
6. Game Detail (renumber)
7. About (renumber)

### B. Model signal front page

Redesign `streamlit_app.py` home into a daily **signal dashboard**:

- System status + freshness warnings
- Signal summary (edge-focused, not PLAY win rate headline)
- Ranked signals table (side-transformed edge in **pp**, signal labels, risk flags)
- Selected game detail + feature context
- Edge distribution buckets
- Model quality snapshot (historical vs prospective separate)
- Finished PLAY results at bottom

**Do not change:** model, PLAY threshold, edge math, prediction records, pipeline.

---

## Allowed files

- `streamlit_app.py`
- `src/app/dashboard_analytics.py`
- `src/app/signal_dashboard.py`
- `src/app/*_page.py` (new observability pages)
- `pages/*.py`
- `src/app/daily_board_page.py` (game detail path only)
- `tests/unit/app/test_dashboard_analytics.py`
- `tests/unit/app/test_signal_dashboard.py`
- `README.md` (sidebar list only)
- `tasks/APP-013-streamlit-observability-dashboards.md`
- `state/task-context/APP-013.md`
- `state/agents/APP-013.md`

## Do not modify

- `src/pipelines/`, `src/models/`, `src/market/engine.py` (market math)
- `src/app/board.py` unless strictly required

---

## Acceptance criteria

- [ ] Sidebar pages separated per spec
- [ ] Home page leads with edge/signals, not PLAY win rate
- [ ] Selected-side display logic is display-only (HOME/AWAY edge in pp)
- [ ] Signal labels: NO EDGE / VALUE ON HOME / VALUE ON AWAY / REVIEW LARGE EDGE
- [ ] Risk flags display-only
- [ ] Historical vs prospective metrics labeled separately
- [ ] Unit tests for helpers; `pytest tests/unit/app/` passes
- [ ] `git diff --check` passes
- [ ] Handoff written to `state/agents/APP-013.md`
