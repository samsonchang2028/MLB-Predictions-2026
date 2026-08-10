# ADR-005: Certification Requires Semantic Content, Not Only Structure

## Status

Accepted.

## Context

The 2021-2025 historical build certified **PASS** while every pitching statistic
in `silver.pitcher_appearances` was 100% NULL across 132,848 rows. The defect
propagated silently:

```
MLB API -> structurally valid response (pitcher identities present, stats objects
empty) -> silver.pitcher_appearances created -> stat columns 100% NULL -> Gold
starter/bullpen features 100% NULL (58 of 211 columns) -> certification PASS ->
models trained without meaningful pitching information
```

Root cause: `GAME_DETAIL_FIELDS` in `src/ingestion/mlb/game_detail.py` is an MLB
`fields=` projection applied at every nesting level. It allowed identity keys
(`person`, `fullName`, `id`) through but omitted the nested pitching stat keys, so
MLB returned `stats: {}` for every player.

Every gate missed it because every gate asked the same kind of question. The
schema was right, the keys were right, the joins were right, determinism held, and
the temporal behavior was correct. Unit and integration tests passed because their
fixtures were hand-authored WITH stats present, so the parser and transform - which
were correct - were the only things under test. The untested seam was the request
we send to the real API. Worse, a test
(`test_players_subtree_is_not_field_filtered`) asserted the stat keys must NOT be
in the projection, locking the defect in place: its premise was half true (listing
`players` does empty the subtree) but wrong for the stat leaf keys.

## Decision

**A dataset is not certified merely because its schema, keys, joins, determinism,
and temporal behavior are valid. Required measurement content must also be
demonstrably present and plausible.**

Certification distinguishes three kinds of validity, and ALL THREE are required
for PASS:

1. structural validity (schema, keys, joins, cardinality, determinism),
2. semantic completeness (required measurements present and plausible),
3. temporal/leakage validity (point-in-time correctness).

Content validation must be baseball-specific and minimal - not a generic
data-quality framework. Thresholds must be documented with reasons, because some
missingness is legitimate (a starter early in 2021 has no prior-season history).
But **100% NULL on any required measurement column hard-fails certification unless
that column is explicitly documented as optional**, and populated-but-degenerate
data (a required stat constant across the dataset, or a measure outside a
documented plausible range) fails as well.

## Defense in depth

No single checkpoint is sufficient on its own. The required chain is:

```
Bronze integrity
-> Silver structural validation
-> Silver semantic completeness
-> temporal/leakage validation
-> Gold feature completeness
-> walk-forward preprocessing isolation
-> final ML evaluation
```

Consequences adopted with this ADR:

- Certification reports required feature families (team, starter, bullpen,
  rest/schedule, and other declared V1 groups) with row count, non-null count,
  null rate, min/max where meaningful, distribution sanity, and PASS/WARN/FAIL
  (DATA-017).
- A Gold pre-model completeness gate runs before ML experiments; a required
  feature family that is effectively absent BLOCKS the experiments (FEAT-006).
- A broken planned feature family must be repaired or explicitly removed from
  methodology through an accepted ADR. **Automatically dropping broken features is
  forbidden**, because silent dropping is what let this incident persist.
- Where a projection or request shapes what we receive, the contract with the real
  upstream must be verified against a real recorded response, and long backfills
  must be preceded by a real-data smoke check over multiple completed games
  (DATA-016). Fixtures we author cannot prove what the API returns.

## Consequences for the existing experiment

The first real experiment (`reports/experiments/v1-real.json`) is **diagnostic
only and invalid for methodology selection**: it ran without meaningful
starter/bullpen signal. Its apparent conclusions (logistic best, expanding beating
rolling, Platt/sigmoid beating isotonic) must be re-derived after repaired
ingestion, certification PASS, Gold completeness PASS, and leakage tests PASS.
Model family, training-window, and calibration-method selection remain UNLOCKED.

2026 remains the untouched final holdout throughout (ADR-003 unchanged).
