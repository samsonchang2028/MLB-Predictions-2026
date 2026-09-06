# MARKET-005 Agent Status

- Task ID: MARKET-005
- Status: DONE / READY TO PUSH
- Branch/worktree: sub-agent forked workspace
- Active role: Orchestrator
- Current activity: finalized for commit/push
- Latest commit: not applicable
- Latest test result: re-test PASS, 89 passed; git diff --check passed
- Blocking issue: none

## Gates

| Gate | Role | Status |
|---|---|---|
| Implementation | implementer | candidate (`01a072a3-1037-7411-9ff8-4e77e4374336`) |
| Review | reviewer | CHANGES REQUIRED, fixed (01a072a7-40b1-7cf2-9c38-8d24efcc10c6) |
| Test | tester | FAIL, fixed (01a072a7-56f2-76a0-9a78-e02906830d4f) |
| Re-review | reviewer | APPROVE (01a077f6-f503-7b01-aaf5-2306016cbede) |
| Re-test | tester | PASS (01a077f7-127c-7b61-9e14-55bc73a916c7) |

## Repair Pass 1

- Repair implementer: 01a07780-589e-78b2-8d73-4f14b0c44ff0
- Required fixes: large-gap classification before agreement/neutral, stale-odds blocking, dashboard risk flags applied before consensus eligibility, invalid American odds treated as unavailable, friendly strategy label, and rendered average odds/subset buckets.

## Scope

Consensus-confirmed shadow strategy:

- classify model/market directional agreement,
- define `consensus_confirmed_play`,
- compare baseline edge-selected PLAY, raw model favorite, and consensus
  challenger,
- update dashboard labels/section without changing production PLAY/PASS or V1
  methodology.

## State Update Limitation

`tasks/index.md` is owned by `mlbpred:mlbpred` and was not writable from this
session, so MARKET-005 is recorded in this status file and task file but not yet
in the task index.

## Implementer Handoff

- Status: implementation candidate ready for review/test.
- Files changed: `src/app/consensus_strategy.py`,
  `src/app/uncertainty_challenger.py`, `src/app/signal_dashboard.py`,
  `src/app/betting_results_page.py`,
  `tests/unit/app/test_consensus_strategy.py`,
  `tests/unit/app/test_signal_dashboard.py`.
- Strategy definitions: baseline PLAY remains `abs(edge) >=
  DEFAULT_EDGE_THRESHOLD`; raw model favorite uses `model_probability >= 0.5`;
  consensus-confirmed PLAY is a SHADOW/DIAGNOSTIC/NOT PRODUCTION candidate on
  the raw model side only when model/market direction agrees or market is
  neutral, selected-side model probability and edge clear thresholds, odds are
  available/fresh when timestamp metadata exists, and no major data warning is
  present.
- Thresholds: `market_neutral_band=0.02`,
  `large_disagreement_threshold=0.08`,
  `min_model_side_probability=0.55`,
  `min_selected_side_edge=0.01`.
- Artifact sources: read-only `state/predictions/daily.jsonl` and
  `state/predictions/journal.jsonl` through existing dashboard JSONL readers.
- Dedupe policy: latest prediction per `game_pk` by `prediction_timestamp`;
  resolved metrics require matching latest journal outcome; pending rows are
  excluded from resolved denominators.
- Dashboard updates: signal labels now distinguish model-market agreement,
  model stronger than market, market contradiction, large disagreement review,
  and no-edge states; Betting Results now includes `Strategy Comparison` with
  baseline, raw model favorite, uncertainty-adjusted challenger, and
  consensus-confirmed challenger plus relationship/probability/edge bucket
  breakdowns.
- Current local artifact metrics: baseline PLAY `n=178`, resolved `169`,
  wins/losses `70/99`, pending `9`, win rate `41.4%`, ROI `-12.3%`, units
  `-20.74`; raw model favorite `n=254`, resolved `237`, wins/losses
  `134/103`, pending `17`, win rate `56.5%`, ROI `-1.5%`, units `-3.48`;
  consensus-confirmed `n=70`, resolved `66`, wins/losses `35/31`, pending
  `4`, win rate `53.0%`, ROI `-6.0%`, units `-3.94`.
- Tests run:
  `PYTHONPATH=/tmp/market005-pytest...:src .venv/bin/python -m pytest tests/unit/app -q`
  -> 156 passed; `git diff --check` -> passed.
- Limitations: pytest was installed into a temporary `/tmp` target because the
  project/system interpreters lacked pytest. `.pytest_cache` could not be
  written due repository permissions, producing a pytest cache warning only.
  `streamlit_app.py` was not writable by this user, so the new explicit display
  section was added to the multipage Betting Results module instead.
