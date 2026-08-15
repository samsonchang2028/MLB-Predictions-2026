# APP-011 — homepage chart-first UI

## Status

ready

## Dependencies

- APP-006

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes — branch `agent/APP-011-homepage-charts`

## Goal

Replace the homepage metric-card wall with a chart-first, friendlier layout:
win-rate hero + slate snapshot charts, minimal model stats, and no Streamlit
chart color errors.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/APP-006-homepage-overview.md`
- `agents/implementer.md`

## Allowed files

- `streamlit_app.py`
- `src/app/homepage.py`
- `tests/unit/app/test_homepage.py`
- `tasks/APP-011-homepage-charts.md`

## May modify if necessary

- `tasks/index.md`
- `state/CURRENT.md`

## Do not modify

- model training/evaluation
- prediction generation
- market/odds math
- daily board pages (unless a one-line import fix is unavoidable)

## Inputs

- `state/predictions/daily.jsonl`
- `state/predictions/journal.jsonl`
- `state/predictions/skipped.jsonl`
- `reports/experiments/v1-holdout-2026.json`

## Outputs

- Chart-first Streamlit homepage
- Testable summary helpers for 7-day play performance and slate snapshot data

## Requirements

1. **Hero — last 7 slates (plays only):**
   - One headline play win rate (%), with wins/losses/pending caption.
   - Bar chart of daily wins vs losses.
   - Line chart of daily win rate on finished plays (omit days with no finished plays).
   - PASS picks excluded from win-rate math (reuse `DEFAULT_EDGE_THRESHOLD`).

2. **Today's slate:**
   - Chart showing Plays / Pass / Awaiting data split.
   - Chart for today's play results: Wins / Losses / Pending (only when plays exist).

3. **Model section (minimal):**
   - Keep at most 3 holdout metrics (log loss, Brier, accuracy).
   - Move freshness timestamps into an expander (not top-level metric cards).

4. **Streamlit chart correctness:**
   - `st.bar_chart` / `st.line_chart` `color=` must match **column count**, not row
     count. Restructure DataFrames so each colored category is its own column
     (e.g. one row with `Wins`, `Losses`, `Pending` columns) — do **not** pass
     multiple colors to a single `count` column.
   - Manually smoke `streamlit run streamlit_app.py` and confirm the homepage
     renders without `StreamlitColorLengthError`.

5. **Data rules:**
   - All numbers artifact-backed via `homepage.py` helpers (testable).
   - Do not recompute model metrics from raw DB.
   - Latest prediction per `game_pk` on each slate (same as APP-006).

6. **User direction (locked):**
   - Mix: win-rate hero + smaller slate snapshot.
   - Win-rate window: last 7 prediction slates.
   - Replace numbers with charts where possible; essential text only.
   - Model holdout: minimal row (2–3 metrics).

## Critical correctness constraints

- PASS/no-play rows must not count as wins or losses.
- Do not claim betting advice or guaranteed returns.
- Win-rate labels must clarify this is displayed play results, not training evidence.

## Acceptance criteria

- Homepage renders without chart color errors.
- Non-technical user sees charts before raw metric grids.
- Unit tests cover 7-day play aggregation, slate snapshot shaping, and holdout
  secondary-metric loading (`metrics.secondary.accuracy`).
- Missing artifacts show guidance, not stack traces.

## Required tests

- unit tests for `play_performance_7d` aggregation (last 7 slates, play-only)
- regression test for latest-per-game collapse on win-rate counts
- missing-artifact test unchanged/passing

## Handoff

Record summary, files changed, commands run, test results, manual Streamlit smoke
(yes/no), known limitations.
