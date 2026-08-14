# APP-009 — plain-English about and methodology page

## Status

backlog

## Dependencies

- APP-006

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Add a plain-English page explaining what the model does, what it does not do,
how to read picks, and why results should be interpreted carefully.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `docs/decisions/ADR-006-v1-methodology-lock.md`
- `tasks/ML-011-model-diagnostics.md`
- `tasks/APP-006-homepage-overview.md`

## Allowed files

- `src/app/`
- `pages/`
- `tests/unit/app/`
- `tasks/APP-009-about-methodology-page.md`

## May modify if necessary

- `README.md`
- `tasks/index.md`
- `state/CURRENT.md`

## Do not modify

- model methodology ADRs unless a factual inconsistency is found
- evaluation artifacts
- prediction generation

## Inputs

- ADR-006 locked methodology
- repaired development report
- final 2026 holdout report
- ML-011 diagnostics report

## Outputs

- A user-facing About/Methodology Streamlit page.

## Requirements

1. Explain in plain language:
   - the model predicts MLB moneyline home-team win probability,
   - the app compares model probability to market-implied probability,
   - “Pick” means market-relative side, not a guaranteed winner,
   - PASS means no displayed play under the current synthetic threshold.
2. State V1 limitations:
   - moneyline only,
   - no totals/props,
   - no weather features yet,
   - no Monte Carlo simulation yet,
   - no Kalshi/arbitrage integration yet,
   - no staking policy.
3. Show core model evidence labels:
   - development/tuning evidence,
   - final 2026 holdout evidence,
   - current daily prediction/result journal.
4. Keep probability-quality metrics primary:
   - log loss,
   - Brier,
   - calibration.
5. Avoid marketing claims that are not backed by artifacts.

## Critical correctness constraints

- Do not claim profitability.
- Do not imply that daily result journal win rate is model-selection evidence.
- Do not imply post-V1 research is part of the locked V1 methodology.

## Acceptance criteria

- A first-time user can understand how to read the app.
- The page clearly separates V1 production behavior from V2 research ideas.
- Tests or snapshot-style checks cover key copy/labels where practical.

## Required tests

- unit tests for any data-shaping helpers used by the page
- smoke/import test if page logic is factored into `src/app`

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- known limitations.
