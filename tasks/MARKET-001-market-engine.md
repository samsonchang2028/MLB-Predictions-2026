# MARKET-001 - Market Probability and Edge Engine

## Status

blocked

## Dependencies

- DATA-009
- ML-008

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Convert moneylines into market probabilities, remove vig, and compare model
probability with market probability.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-002-point-in-time.md`
- `docs/decisions/ADR-004-historical-data-and-certification.md`
- `tasks/DATA-009-odds-archive-validation.md`

## Allowed files

- `src/market/`
- `tests/unit/market/`

## Requirements

- American odds conversion,
- two-way no-vig normalization,
- edge calculation,
- expected-value calculation,
- preserve odds timestamp used for live/future predictions,
- support historical opening-line benchmark inputs from DATA-009,
- label historical archive ROI as simulated ROI at opening prices.

## Critical constraints

- no future/closing odds in historical pregame prediction unless timestamp-valid,
- historical archive opening odds support model edge versus opening market only,
- do not claim exact historical price at prediction time from the archive,
- closing/current-style archive odds may only be post-hoc benchmarks,
- formulas covered by deterministic tests.
