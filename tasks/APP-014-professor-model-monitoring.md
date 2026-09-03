# APP-014 - professor-readable model monitoring tab

## Status

candidate

## Dependencies

- APP-013 - separated Streamlit observability dashboards
- OBS-002 - result-enrichment journal
- ML-010 - final 2026 holdout report
- ML-011 - diagnostics report
- ML-015 - prospective diagnostic

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes when available

## Goal

Add a readable Streamlit view that helps explain the project to a data science
professor and shows daily live monitoring results without mixing betting
selection results into model-quality evidence.

## Allowed files

- `src/app/dashboard_analytics.py`
- `src/app/model_quality_page.py`
- `tests/unit/app/test_dashboard_analytics.py`
- `tests/unit/app/test_model_quality_page.py` if needed
- `tasks/APP-014-professor-model-monitoring.md`
- `state/task-context/APP-014.md`
- `state/agents/APP-014.md`

## Do not modify

- `src/pipelines/`
- `src/models/`
- `src/market/engine.py`
- prediction or journal artifact schemas
- model methodology / ADR-006

## Requirements

1. Add a professor-readable tab or equivalent section to Model Quality.
2. Explain:
   - the model predicts `P(home team wins)`,
   - PLAY/PASS is a separate market-selection rule,
   - raw/Bronze payloads are immutable,
   - certified Silver/Gold data gates model work,
   - features are point-in-time safe,
   - validation is chronological,
   - 2026 was held out from selection/tuning.
3. Show primary model-quality metrics clearly:
   - log loss,
   - Brier,
   - ECE/calibration.
4. Keep secondary metrics labeled separately:
   - ROC-AUC,
   - accuracy.
5. Add daily prospective monitoring by `run_date`:
   - games,
   - resolved,
   - pending,
   - daily probability metrics when available,
   - PLAY count,
   - PLAY wins/losses/pending,
   - PLAY win rate,
   - flat 1-unit ROI/units when odds are present.
6. Pending games must be excluded from daily win rate and ROI.
7. PASS rows must not enter PLAY win rate.
8. Do not claim profitability or betting advice.

## Acceptance criteria

- Model Quality page is readable for a non-project professor.
- Historical/development/holdout/prospective evidence remain separated.
- Daily win-rate table is artifact-backed from `daily.jsonl` + `journal.jsonl`.
- Focused unit tests cover daily grouping, latest-snapshot behavior, pending
  rows, PLAY-only denominators, and missing odds.
- `pytest tests/unit/app/test_dashboard_analytics.py` passes.
- `git diff --check` passes.

