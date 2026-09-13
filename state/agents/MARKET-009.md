# MARKET-009 Agent Status

| Field | Value |
|-------|-------|
| Task | MARKET-009 — Prospective closing odds capture |
| Branch | `agent/MARKET-009-closing-odds-capture` |
| Status | **MERGED** (`main` @ ba72c86) |
| Implementer | [complete](c895fc39-d00e-4299-8812-ccc68cd0d255) + [P1 repair](03091bc3-9bc9-4469-a4a5-181410f09422) |
| Reviewer | **APPROVE** ([6ce4bef2-d52c-411f-8e42-918d81d69af5](6ce4bef2-d52c-411f-8e42-918d81d69af5)) |
| Tester | **PASS** ([39cd66cc-2374-48d8-929e-11ec3296ac8e](39cd66cc-2374-48d8-929e-11ec3296ac8e)) — 38/38 |

## CLV rerun (earliest anchor)

Strict N=113 · Case 2 (positive CLV, negative ROI) · ADR-007 still blocked

## Gate checklist

- [x] odds_closes + capture CLI
- [x] CLV reads odds_closes
- [x] strict N ≥ 30
- [x] Reviewer APPROVE
- [x] Tester PASS
- [x] `state/predictions/odds_closes.jsonl` installed on host (109 rows via OPS-004 backfill)
- [x] merge to main (`ba72c86`)

## Operator follow-up

```bash
# as mlbpred on host
cd /opt/mlb-predictions
.venv/bin/python scripts/closing_odds_capture.py --backfill-from-odds-books --anchor earliest
```
