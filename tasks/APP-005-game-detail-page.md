# APP-005 — Game Detail Page

## Status

candidate

## Dependencies

- PIPE-004
- APP-004

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes` (`agent/PIPE-004-game-detail-artifacts`, same branch
as PIPE-004 since this task consumes its artifacts directly)

## Goal

Give a user a way to click into one game from the daily board and see the
starter/bullpen/team feature values behind the prediction, plus a multi-book
odds comparison, without requiring DuckDB access (works on the deployed
Streamlit Cloud app, artifact-backed like the rest of `app/`).

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `src/app/board.py`, `src/app/daily_board_page.py` (existing display
  conventions this mirrors)
- `src/market/engine.py` (reused for odds -> implied probability; not
  reimplemented)
- `tasks/PIPE-004-prediction-detail-artifacts.md` (the two artifacts this
  reads)

## Allowed files

- `src/app/game_detail.py` (new)
- `src/app/game_detail_page.py` (new)
- `pages/3_Game_Detail.py` (new)
- `tests/unit/app/test_app_game_detail.py` (new)
- `src/app/daily_board_page.py` (small, targeted click-through diff)

## May modify if necessary

- none

## Do not modify

- `src/app/board.py` (reused via import, not duplicated or changed)
- `src/pipelines/daily.py`, `scripts/daily_predictions.py` (PIPE-004's
  contract; this task only reads its outputs)

## Inputs

- `state/predictions/daily.jsonl` (existing PIPE-001 record, via
  `JsonLinesPredictionStore`)
- `state/predictions/game_features.jsonl`, `state/predictions/odds_books.jsonl`
  (PIPE-004 artifacts; both optional -- see Requirements)

## Outputs

- `src/app/game_detail.py`: `load_game_detail(game_pk, run_date, *,
  predictions_store, features_path, odds_books_path)` -- pure, no Streamlit
  import, returns `None` when no matching prediction exists.
- `src/app/game_detail_page.py` + `pages/3_Game_Detail.py`: Streamlit page
  reading `game_pk`/`run_date` from `st.query_params`.
- Click-through: selecting a row on the daily board's `st.dataframe`
  (`on_select="rerun"`, `selection_mode="single-row"`) sets those query
  params and calls `st.switch_page("pages/3_Game_Detail.py")`.

## Requirements

- Missing PIPE-004 artifacts (predictions made before that task shipped, or
  a fresh checkout with no `game_features.jsonl`/`odds_books.jsonl` yet) are
  tolerated, not errors: `load_game_detail` returns `features=None` /
  `odds_books=[]`, and the page shows an `st.info(...)` fallback per section
  -- same tolerance pattern as `daily_board_page.py`'s `path.exists()` guard.
- Feature values are grouped by component (`team`/`starter`/`bullpen`) by
  parsing the existing `home_/away_/diff_{component}_{key}` column-naming
  convention from `features.build.build_feature_matrix` -- there is no
  separate curated display-name list anywhere in the codebase to read
  instead.
- Odds-by-book implied probabilities use `market.no_vig_two_way` (MARKET-001)
  verbatim; no odds math is reimplemented here.
- The model's displayed `model_probability`/`market_probability`/`edge` are
  the canonical PIPE-001 values, unaffected by how many comparison books are
  shown.

## Critical correctness constraints

- No DuckDB import/usage anywhere in `game_detail.py` or `game_detail_page.py`
  -- this must work identically against committed artifacts whether run
  locally or on Streamlit Community Cloud (no local DB access there).
- `_group_features` must not silently drop or misattribute a feature column;
  unit-tested against representative `home_/away_/diff_` column names for
  all three components.

## Acceptance criteria

- Clicking a row on the daily board navigates to the detail page for that
  exact `game_pk`/`run_date` and shows matching feature values and a
  multi-book odds table when artifacts exist.
- Loading the detail page directly via `?game_pk=...&run_date=...` for a
  game with no PIPE-004 artifacts shows the graceful fallback messages
  instead of raising.
- Full existing test suite (`tests/unit/app`, `tests/unit/scripts`, and the
  rest of the repo) is unaffected.

## Required tests

- unit (`tests/unit/app/test_app_game_detail.py`): missing prediction ->
  `None`; missing artifacts tolerated; feature grouping by component; odds
  comparison implied-probability + latest-snapshot-per-book selection.

## Handoff

- Added `src/app/game_detail.py` (`load_game_detail`), reusing
  `app.board`'s `_team_label`/`_matchup`/`_format_pacific` and
  `market.no_vig_two_way` rather than duplicating either.
- Added `src/app/game_detail_page.py` + `pages/3_Game_Detail.py` following
  the existing thin-runpy-wrapper convention.
- Wired click-through in `src/app/daily_board_page.py`: the board's
  `st.dataframe` now uses `on_select="rerun"`/`selection_mode="single-row"`;
  a selection sets `st.query_params["game_pk"]`/`["run_date"]` and calls
  `st.switch_page("pages/3_Game_Detail.py")`.
- Test file is named `test_app_game_detail.py`, not `test_game_detail.py` --
  `tests/unit/ingestion/mlb/test_game_detail.py` already exists (unrelated,
  DATA-005) and this repo's tests have no `__init__.py` markers, so pytest's
  rootdir-based collection would otherwise hit an "import file mismatch" on
  the duplicate basename across the two directories. Confirmed by running
  the full suite once with the collision (failed) and once renamed (630
  passed).
- Commands run: `python -m pytest -q` (full suite) — 630 passed.
- Built in an isolated worktree (`agent/PIPE-004-game-detail-artifacts`)
  since APP-001A's own in-flight work briefly collided with a first attempt
  directly on `main` (see PIPE-004's handoff); no functional loss, just a
  redo in isolation.
- Additionally smoke-tested both pages with `streamlit.testing.v1.AppTest`
  (headless script execution, no browser extension available this session):
  the daily board renders with zero exceptions; the detail page loaded via
  `?game_pk=...&run_date=...` against synthetic artifacts renders the
  matchup header, all three metrics, and 4 dataframes (3 feature-component
  tables + 1 odds-by-book table) with zero exceptions; the detail page with
  no query params shows the correct fallback info message. Synthetic
  fixtures were deleted afterward, not committed.
- Known limitations: no literal mouse-click browser verification this
  session (no browser extension connected); the `on_select`/`switch_page`
  click-through wiring itself is exercised only by code review against
  Streamlit 1.61's documented API, not by AppTest (which doesn't simulate
  dataframe row selection). Recommend one manual click-through pass in a
  real browser before merging.
- No ADR change required. No `state/CURRENT.md` update yet — pending review
  gate per repo convention.
