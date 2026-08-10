# ML-004A — Expose Per-Fold Predictions From the Walk-Forward Runner

## Status

ready

## Dependencies

- ML-004

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Rationale

ML-005 (expanding) and ML-006 (rolling) must emit a per-game prediction table,
and ML-007 must join predictions to odds by `game_pk` (calibration behavior,
market-relative metrics, simulated ROI). The ML-004 runner currently returns
only metrics and internally drops unlabeled test rows, so per-game predictions
cannot be recovered by callers without fragile re-implementation of the runner's
row ordering + unlabeled-drop. Expose predictions once, in the framework, so both
parallel experiments consume a single stable schema.

## Goal

Add an opt-in per-fold prediction table to `run_evaluation` in
`src/evaluation/runner.py`, keyed by canonical `game_pk`.

## Requirements

- `run_evaluation(..., return_predictions: bool = False)`. Default False keeps the
  current return shape and behavior byte-for-byte (existing tests must stay green).
- When True, each per-fold report gains a `predictions` list; each entry is
  `{game_pk, p_home_win, y_true}` for that fold's LABELED test rows only,
  correctly aligned to the probabilities/labels after the existing unlabeled-drop.
- `game_pk` is the canonical FEAT-004 row identity; ordering deterministic and
  consistent with the runner's chronological row order.
- No change to metrics, folds, or aggregate. No new dependency. Smallest change.

## Acceptance criteria

- with `return_predictions=True`, fold `predictions` length equals the fold's
  labeled test-row count, and each `game_pk`/`p_home_win`/`y_true` triple is
  correctly aligned (verified against a known synthetic matrix),
- default call unchanged (regression),
- deterministic,
- full suite green.
