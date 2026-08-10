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
| DATA-007 | done | DATA-006 | merged; certification artifact layer (`src/validation/certification.py` + `state/data-certifications/`). NOTE: a real 2021-2025 build must be ingested + certified PASS before FEAT-002/003 |
| DATA-008 | done | DATA-001 | merged 479af32; checksum-verified historical odds archive ingestion |
| DATA-009 | done | DATA-004, DATA-008 | merged; odds->game_pk mapping audit + odds validation + coverage report (`src/validation/odds_mapping.py`) |
| DATA-010 | done | DATA-005 | merged a87ef2b; MLB backfill restart resilience (reused run_id upsert + per-game integrity isolation) |
| DATA-011 | done | DATA-005/006/007/008/009 | merged; MLB-StatsAPI fetchers + `pipelines/certify_historical.py` runner. Real multi-hour build is operator-run |
| DATA-012 | done | DATA-006 | merged; fix results.valid_scores for postponed games reported as abstract Final (found via DATA-011 real smoke) |
| DATA-013 | done | DATA-002 | merged; reconcile repeated game_pk in season schedule responses (postponed+rescheduled Final), conflicts FAIL. Found via DATA-011 real build |
| DATA-014 | done | DATA-002, DATA-013 | merged; reconcile suspended/resumed same-Final duplicate game_pk (same outcome), real outcome conflicts still FAIL. Found via DATA-011 real build |
| DATA-015 | done | DATA-006 | merged; home_win_derivation scoped to regular season (spring ties no longer FAIL cert), regular-season strictness preserved. Found via DATA-011 full build |
| FEAT-001 | done | DATA-004 | completed; downstream real-dataset use gated by DATA-007 |
| FEAT-002 | done | DATA-007 | merged; point-in-time starter features (`src/features/starter.py`), leakage-tested |
| FEAT-003 | done | DATA-007 | merged; point-in-time bullpen features (`src/features/bullpen.py`), leakage-tested |
| FEAT-004 | done | DATA-007, FEAT-001/002/003 | merged; game feature matrix (`src/features/build.py`), target isolated, leakage-tested |
| ML-001 | done | FEAT-004 | merged; logistic regression (`src/models/logistic.py`), shared contract |
| ML-002 | done | FEAT-004 | merged; random forest (`src/models/random_forest.py`), shared contract |
| ML-003 | done | FEAT-004 | merged; XGBoost (`src/models/xgboost_model.py`), shared contract |
| ML-004 | done | ML-001/002/003 | merged; walk-forward framework (`src/evaluation/`), drives all 3 families; leakage-tested |
| ML-004A | done | ML-004 | merged; game_pk-keyed per-fold predictions in runner (`return_predictions`) |
| ML-005 | done | ML-004A | merged; expanding-window experiment (`src/experiments/expanding.py`), shared schema |
| ML-006 | done | ML-004A | merged; rolling 2/3-season experiments (`src/experiments/rolling.py`), shared schema |
| ML-007 | in-progress | ML-005/006 | dispatched; model x window comparison (`src/experiments/comparison.py`); 2026 not used |
| ML-008 | blocked | ML-007 | calibration |
| MARKET-001 | blocked | DATA-009, ML-008 | market engine; historical opening benchmark plus live timestamped odds |
| PIPE-001 | blocked | MARKET-001 | daily production |
| OBS-001 | blocked | PIPE-001 | parallel with APP-001 |
| APP-001 | blocked | PIPE-001 | parallel with OBS-001 |
| APP-002 | blocked | APP-001, OBS-001 | final V1 UI |

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
