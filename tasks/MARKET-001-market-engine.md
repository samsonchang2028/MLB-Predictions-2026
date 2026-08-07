# MARKET-001 — Market Probability and Edge Engine

## Status

blocked

## Dependencies

- DATA-004
- ML-008

## Execution

Primary role: implementer  
Review required: yes  
Tester required: yes  
Worktree required: yes

## Goal

Convert moneylines into market probabilities, remove vig, and compare model probability with market probability.

## Allowed files

- `src/market/`
- `tests/unit/market/`

## Requirements

- American odds conversion,
- two-way no-vig normalization,
- edge calculation,
- expected-value calculation,
- preserve odds timestamp used.

## Critical constraints

- no future/closing odds in historical pregame prediction unless timestamp-valid,
- formulas covered by deterministic tests.
