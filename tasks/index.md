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
| FEAT-002 | in-progress | DATA-007 | dispatched; point-in-time starter features (`src/features/starter.py`); parallel with FEAT-003 |
| FEAT-003 | in-progress | DATA-007 | dispatched; point-in-time bullpen features (`src/features/bullpen.py`); parallel with FEAT-002 |
| FEAT-004 | blocked | DATA-007, FEAT-001/002/003 | integration |
| ML-001 | blocked | FEAT-004 | parallel with ML-002/003 |
| ML-002 | blocked | FEAT-004 | parallel with ML-001/003 |
| ML-003 | blocked | FEAT-004 | parallel with ML-001/002 |
| ML-004 | blocked | ML-001/002/003 | shared validator |
| ML-005 | blocked | ML-004 | parallel with ML-006 |
| ML-006 | blocked | ML-004 | parallel with ML-005 |
| ML-007 | blocked | ML-005/006 | comparison |
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
