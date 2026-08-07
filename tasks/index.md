# Task Index

The task graph is defined conceptually in `docs/roadmap.md`.

This index lists task state and dependency relationships in a human-readable format.

| Task | Status | Depends on | Parallel notes |
|---|---|---|---|
| META-001 | done | — | completed |
| DATA-001 | done | META-001 | completed |
| DATA-002 | implementing | DATA-001 | parallel with DATA-003 |
| DATA-003 | implementing | DATA-001 | parallel with DATA-002 |
| DATA-004 | blocked | DATA-002, DATA-003 | integration |
| FEAT-001 | blocked | DATA-004 | parallel with FEAT-002/003 |
| FEAT-002 | blocked | DATA-004 | parallel with FEAT-001/003 |
| FEAT-003 | blocked | DATA-004 | parallel with FEAT-001/002 |
| FEAT-004 | blocked | FEAT-001/002/003 | integration |
| ML-001 | blocked | FEAT-004 | parallel with ML-002/003 |
| ML-002 | blocked | FEAT-004 | parallel with ML-001/003 |
| ML-003 | blocked | FEAT-004 | parallel with ML-001/002 |
| ML-004 | blocked | ML-001/002/003 | shared validator |
| ML-005 | blocked | ML-004 | parallel with ML-006 |
| ML-006 | blocked | ML-004 | parallel with ML-005 |
| ML-007 | blocked | ML-005/006 | comparison |
| ML-008 | blocked | ML-007 | calibration |
| MARKET-001 | blocked | DATA-004, ML-008 | market engine |
| PIPE-001 | blocked | MARKET-001 | daily production |
| OBS-001 | blocked | PIPE-001 | parallel with APP-001 |
| APP-001 | blocked | PIPE-001 | parallel with OBS-001 |
| APP-002 | blocked | APP-001, OBS-001 | final V1 UI |

## Status transitions

```text
backlog
  ↓
ready
  ↓
implementing
  ↓
candidate
  ├─ reviewer
  └─ tester
       ↓
changes_requested ↺
       ↓
approved
       ↓
done
```

Use `blocked` whenever an unmet dependency or unresolved decision prevents execution.
