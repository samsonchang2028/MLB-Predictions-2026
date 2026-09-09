# MARKET-007 Context Pack

## Status

**BLOCKED** until MARKET-006 merges with `src/market/play_policy.py` + study report.

## Objective

Dashboard observability for baseline vs shadow policy. No production PLAY change.

## Depends on MARKET-006 handoff

- Shadow candidate policy name/version from study report
- `src/market/play_policy.py` API: `evaluate_policy`, `classify_game`, `policy_reason_codes`
- Population semantics must match study (latest per game_pk)

## UI requirements

Per game: raw model fav, market fav, agreement, edge, **BASELINE PLAY/PASS**,
**SHADOW CANDIDATE** / **NO CANDIDATE**, reason codes, risk flags.

Comparison panel: baseline vs shadow vs raw model favorite — N, win rate, ROI,
units, avg odds, pending, sample period.

Copy must say shadow is **NOT PRODUCTION** / **NOT A BETTING RECOMMENDATION**.

## Do not

- Change `DEFAULT_EDGE_THRESHOLD`
- Mutate `state/predictions/*.jsonl`
- Promote challenger without ADR-007 acceptance

## Tests

`tests/unit/app/test_play_policy_display.py` + existing board regression tests.
