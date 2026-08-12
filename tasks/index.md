# Task Index

The task graph is defined conceptually in `docs/roadmap.md`.

This index lists task state and dependency relationships in a human-readable
format.

| Task | Status | Depends on | Parallel notes |
|---|---|---|---|
| META-001 | done | - | completed |
| DATA-001 | done | META-001 | completed |
| DATA-002 | done | DATA-001 | completed |
| DATA-003 | done | DATA-001 | completed live timestamped odds ingestion |
| DATA-004 | done | DATA-002, DATA-003 | completed Silver schedule/live-odds contracts |
| DATA-005 | done | DATA-004 | merged e50747c; MLB game-detail/pitcher backfill |
| DATA-006 | done | DATA-005 | merged 1d9b83b; validation package + side-effect-free certification runner |
| DATA-007 | done | DATA-006 | certification artifact layer; real 2021-2025 build certified PASS |
| DATA-008 | done | DATA-001 | merged 479af32; checksum-verified historical odds archive ingestion |
| DATA-009 | done | DATA-004, DATA-008 | odds->game_pk mapping audit + odds validation + coverage report |
| DATA-010 | done | DATA-005 | merged a87ef2b; restart resilience |
| DATA-011 | done | DATA-005/006/007/008/009 | MLB-StatsAPI fetchers + historical certification runner |
| DATA-012 | done | DATA-006 | postponed/suspended/cancelled games reported as abstract Final no longer fail valid_scores |
| DATA-013 | done | DATA-002 | repeated season-response game_pk reconciliation |
| DATA-014 | done | DATA-002, DATA-013 | suspended/resumed same-Final duplicate game_pk reconciliation |
| DATA-015 | done | DATA-006 | regular-season home_win derivation scope |
| DATA-016 | done | DATA-011 | projection fix + lifecycle-aware hollow-payload guard |
| DATA-017 | done | DATA-007 | certification semantic-completeness gate |
| DATA-018 | done | DATA-016, DATA-010 | hollow invalidation + real 2021-2025 re-ingest completed; repaired certification PASS |
| FEAT-001 | done | DATA-004 | completed team features |
| FEAT-002 | done | DATA-007 | point-in-time starter features |
| FEAT-003 | done | DATA-007 | point-in-time bullpen features |
| FEAT-004 | done | DATA-007, FEAT-001/002/003 | game feature matrix |
| FEAT-005 | done | FEAT-004 | component-coverage exclusion policy |
| FEAT-006 | done | FEAT-004 | Gold pre-model completeness gate |
| ML-001 | done | FEAT-004 | logistic regression |
| ML-002 | done | FEAT-004 | random forest |
| ML-003 | done | FEAT-004 | XGBoost |
| ML-004 | done | ML-001/002/003 | walk-forward framework |
| ML-004A | done | ML-004 | game_pk-keyed per-fold predictions |
| ML-005 | done | ML-004A | expanding-window experiment |
| ML-006 | done | ML-004A | rolling 2/3-season experiments |
| ML-007 | done | ML-005/006 | model x window comparison; 2026 unused |
| ML-008 | done | ML-007 | calibration with inner calibration split |
| ML-009 | ready | ML-008, FEAT-006 | methodology lock decision; must not inspect 2026 |
| ML-010 | blocked | ML-009 | untouched 2026 final holdout evaluation |
| MARKET-001 | done | DATA-009, ML-008 | no-vig, edge, EV; timestamp-guarded; opening benchmark labeled |
| PIPE-001 | done | MARKET-001 | daily pipeline |
| OBS-001 | done | PIPE-001 | append-only prediction journal merged |
| APP-001 | done | PIPE-001 | Streamlit daily board merged |
| APP-002 | ready | APP-001, OBS-001 | performance dashboard; can run after methodology lock if it needs final model evidence |

## Current orchestration gate

ML-009 is the critical next task. ML-010 and any 2026 inspection remain blocked
until ML-009 records a locked methodology decision.

APP-002 is dependency-ready from an application graph perspective, but it should
not present final model conclusions until ML-009/ML-010 artifacts exist.

## Status transitions

```text
backlog
  |
  v
ready
  |
  v
implementing
  |
  v
candidate
  +--> reviewer
  +--> tester
        |
        v
changes_requested
        |
        v
approved
        |
        v
done
```

Use `blocked` whenever an unmet dependency or unresolved decision prevents
execution.
