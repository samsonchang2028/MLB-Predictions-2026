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
| DATA-005 | ready | DATA-004 | next core MLB unblocker; can run with DATA-008 |
| DATA-006 | blocked | DATA-005 | validation before feature/model dependency readiness |
| DATA-007 | blocked | DATA-006 | certification gate for 2021-2025 MLB data |
| DATA-008 | ready | DATA-001 | historical odds archive ingestion; can run with DATA-005 |
| DATA-009 | blocked | DATA-004, DATA-008 | historical odds archive validation and `game_pk` mapping audit |
| FEAT-001 | done | DATA-004 | completed; downstream real-dataset use gated by DATA-007 |
| FEAT-002 | blocked | DATA-007 | needs certified pitcher appearance data |
| FEAT-003 | blocked | DATA-007 | needs certified pitcher appearance data |
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
