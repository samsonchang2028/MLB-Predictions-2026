# ADR-002: Point-in-Time Correctness

## Status

Accepted.

## Context

MLB predictive models can appear strong when current-game or future information leaks into historical feature rows.

## Decision

Every feature row must be reproducible using only information available at its prediction timestamp.

Rules include:

- current game excluded from rolling aggregates,
- future games excluded,
- future pitcher appearances excluded,
- future odds snapshots excluded,
- all fit operations restricted to training partitions,
- prediction timestamp must precede first pitch.

Leakage tests are merge-blocking.

## Consequences

Feature builders must carry explicit time/order semantics. Some implementation convenience is sacrificed for trustworthy evaluation.
