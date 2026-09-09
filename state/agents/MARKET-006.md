# MARKET-006 Agent Status

| Field | Value |
|---|---|
| Task | MARKET-006 — Production PLAY policy study |
| Status | **APPROVED** (merge-ready, uncommitted) |
| Branch | agent/MARKET-006-play-policy-study |
| Reviewer | APPROVE (repair re-review) |
| Tester | PASS (125 passed, 4 xfailed) |
| Verdict | **NO POLICY READY FOR PRODUCTION** |

## Key results (N=278)

| Slice | Win rate | ROI |
|---|---:|---:|
| Baseline PLAY (2%) | 40.3% | -14.5% |
| Large disagreement (8pp+) | 35.1% | -22.1% |
| Crossover plays | 33.7% | -16.0% |

135 candidates tested; none passed production gates.

## Artifacts

- `scripts/market_policy_study.py`
- `src/market/play_policy.py`, `policy_evaluation.py`
- `reports/market/play-policy-study-market-006.{json,md}`
- `docs/decisions/ADR-007-play-staking-policy.md` (PROPOSED)

## Merge note

Combined with MARKET-007 on same branch. Commit + merge when operator ready.
