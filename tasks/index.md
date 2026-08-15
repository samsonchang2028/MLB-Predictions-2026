# Task Index

The task graph is defined conceptually in `docs/roadmap.md`. This file is the
human-readable status index; `state/CURRENT.md` remains the detailed project
state source of truth.

## Completed V1 graph

| Task | Status | Depends on | Notes |
|---|---|---|---|
| META-001 | done | - | repository/agent foundation |
| DATA-001 | done | META-001 | DuckDB/Parquet storage |
| DATA-002 | done | DATA-001 | MLB schedule ingestion |
| DATA-003 | done | DATA-001 | live timestamped odds ingestion |
| DATA-004 | done | DATA-002, DATA-003 | normalized Silver datasets/contracts |
| DATA-005 | done | DATA-004 | MLB game-detail/pitcher backfill |
| DATA-006 | done | DATA-005 | validation package |
| DATA-007 | done | DATA-006 | certification artifact layer |
| DATA-008 | done | DATA-001 | historical odds archive ingestion |
| DATA-009 | done | DATA-004, DATA-008 | odds->game_pk mapping audit |
| DATA-010 | done | DATA-005 | game-detail restart resilience |
| DATA-011 | done | DATA-005/006/007/008/009 | real-build certification runner |
| DATA-012 | done | DATA-006 | postponed/suspended/cancelled Final score handling |
| DATA-013 | done | DATA-002 | repeated season-response game_pk reconciliation |
| DATA-014 | done | DATA-002, DATA-013 | suspended/resumed duplicate game_pk reconciliation |
| DATA-015 | done | DATA-006 | regular-season home_win derivation scope |
| DATA-016 | done | DATA-011 | game-detail pitching-stat projection repair |
| DATA-017 | done | DATA-007 | semantic completeness certification |
| DATA-018 | done | DATA-016, DATA-010 | hollow invalidation + full repaired re-ingest |
| FEAT-001 | done | DATA-004 | team features |
| FEAT-002 | done | DATA-007 | starter features |
| FEAT-003 | done | DATA-007 | bullpen features |
| FEAT-004 | done | DATA-007, FEAT-001/002/003 | game feature matrix |
| FEAT-005 | done | FEAT-004 | component-coverage exclusion policy |
| FEAT-006 | done | FEAT-004 | Gold feature completeness gate |
| ML-001 | done | FEAT-004 | logistic regression |
| ML-002 | done | FEAT-004 | random forest |
| ML-003 | done | FEAT-004 | XGBoost |
| ML-004 | done | ML-001/002/003 | walk-forward framework |
| ML-004A | done | ML-004 | game_pk-keyed per-fold predictions |
| ML-005 | done | ML-004A | expanding-window experiment |
| ML-006 | done | ML-004A | rolling-window experiments |
| ML-007 | done | ML-005/006 | model/window comparison |
| ML-008 | done | ML-007 | calibration comparison |
| ML-009 | done | ML-008, FEAT-006 | ADR-006 methodology lock |
| ML-010 | done | ML-009 | final 2026 holdout evaluated once |
| MARKET-001 | done | DATA-009, ML-008 | no-vig/edge/EV engine |
| PIPE-001 | done | MARKET-001 | daily prediction pipeline core |
| OBS-001 | done | PIPE-001 | append-only prediction journal |
| APP-001 | done | PIPE-001 | Streamlit daily board |
| APP-002 | done | APP-001, OBS-001 | performance dashboard with final holdout evidence |
| PIPE-002 | done | PIPE-001, ML-009 | local daily operator |
| PIPE-003 | done | PIPE-002 | live odds matching + unknown-starter hardening |
| APP-003 | done | APP-001, PIPE-002 | daily board Pacific time/model-side display |
| DATA-019 | done | DATA-018 | zero pitcher-line local investigation report |
| DOCS-001 | done | V1 completion | README/task-index status cleanup |
| DATA-020 | done | DATA-019 | inactive zero-line pitcher handling; no re-fetch performed |

## Current optional graph candidates

| Task | Status | Depends on | Notes |
|---|---|---|---|
| APP-004 | done | APP-003 | Streamlit deployment packaging |
| ML-011 | done | ML-010 | model diagnostics report for underfit/overfit evidence |
| DATA-021 | backlog | DATA-020 | targeted retry/normalization of the 39 DATA-018 games |
| OPS-001 | backlog | APP-004, PIPE-003 | scheduled daily operator / GitHub Actions; needs secret + data artifact strategy |
| OBS-002 | candidate | OBS-001, PIPE-003, PIPE-005 | result enrichment operator for daily predictions after games finish |
| MARKET-002 | backlog | MARKET-001, OBS-002 | persisted market-relative report/ROI artifact |
| APP-006 | candidate | APP-004, APP-005, OBS-002 | user-friendly homepage overview with model/status/result summary cards |
| APP-007 | candidate | APP-006, OBS-002 | friendlier daily picks board for non-technical users |
| APP-008 | candidate | APP-007 | best plays of the day chart/ranking |
| APP-009 | candidate | APP-006 | plain-English about/methodology page |
| APP-011 | done | APP-006 | chart-first homepage (merged) |
| API-001 | ready | APP-001 | minimal read-only FastAPI prediction adapter |
| APP-001A | done | APP-001 | malformed/stale prediction-record hardening for xfail-pinned P2 |
| OPS-002 | done | - | git remote updated to github.com/samsonchang2028/MLB-Predictions-2026 |
| PIPE-004 | merged, self-reviewed only | PIPE-003, DATA-003 | persist per-game feature breakdown + multi-book odds comparison artifacts |
| APP-005 | merged, self-reviewed only | PIPE-004, APP-004 | Streamlit game detail page (pitcher/bullpen stats, multi-book odds) reachable from the daily board |
| PIPE-005 | done | PIPE-003, DATA-018 | refresh today's MLB game-detail payloads before daily predictions (probable starters) |
| DATA-022 | done | DATA-001 | Kalshi MLB market data ingestion (Bronze, public API, no auth) — merged, Reviewer APPROVE + Tester PASS, 1 non-blocking P2 xfail-pinned (>4-decimal price rounding) |
| DATA-023 | done | DATA-022, DATA-002 | Kalshi event -> game_pk matching — merged; Reviewer P1 (silent wrong-match on same-city club collisions) fixed + re-reviewed APPROVE, Tester PASS |
| MARKET-003 | done | MARKET-001 | probability<->American-odds helper so Kalshi reuses the existing odds_books.jsonl schema — merged, Reviewer APPROVE + Tester PASS, 1 non-blocking P2 xfail-pinned (OverflowError below ~5.5e-307) |
| PIPE-006 | ready | DATA-022, DATA-023, MARKET-003, PIPE-004 | capture each game's Kalshi price near that game's first pitch, not on the daily batch timestamp; needs a Bronze schema addition or fetch-time matching since DATA-022 doesn't persist occurrence_datetime/title/no_sub_title (flagged by DATA-023's Implementer) |
| APP-012 | blocked | PIPE-006, APP-005 | show Kalshi in the existing odds-by-book comparison table (renamed from APP-010 to avoid collision with the merged V2 simulation-dashboard APP-010) |
| ML-012 | ready | ML-009, FEAT-006 | feature-family ablation study (KEEP/INVESTIGATE/REMOVE per family, top-3 next-experiment recommendations); does not change production model selection |

## V2 simulation graph (team-level Monte Carlo — no player props)

| Task | Status | Depends on | Notes |
|---|---|---|---|
| SIM-000 | done | FEAT-004, ML-010 | game-level team score Monte Carlo from existing Gold features |
| DATA-024 | done | DATA-003, DATA-004 | totals (over/under) odds ingestion |
| SIM-001 | backlog | SIM-003, ML-009 | walk-forward validate sim P(home_win) vs locked XGBoost |
| SIM-002 | ready | SIM-000, MARKET-001 | totals / runs-per-game probs + edge from simulation |
| SIM-003 | ready | SIM-000 | full Gold feature score model |
| PIPE-007 | ready | SIM-003, PIPE-005 | daily operator simulation.jsonl artifacts |
| APP-010 | ready | PIPE-007 | Streamlit simulation comparison tab + charts |
| DOCS-002 | backlog | SIM-001 | ADR-007 V2 policy: XGBoost owns ML, sim owns totals |

Wave 2 parallel: **SIM-003** + **SIM-002**.
Wave 3 parallel (after SIM-003 merge): **PIPE-007** + **APP-010**.

## Safe parallel guidance

Current safe parallel set:

```text
DATA-019 data investigation (completed)
DOCS-001 docs/status cleanup (completed)
ML-011 model diagnostics (completed)
```

Avoid parallelizing tasks that both change prediction journal schema or the same
Streamlit board files.

## Status transitions

```text
backlog -> ready -> implementing -> candidate -> reviewer/tester -> approved -> done
```

Use `blocked` whenever an unmet dependency or unresolved decision prevents
execution.
