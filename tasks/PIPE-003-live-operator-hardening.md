# PIPE-003 - live operator hardening

## Status

Completed.

## Role

Implementer.

## Scope

Harden the local daily prediction operator for realistic live pregame conditions:

- tolerate small The Odds API commence-time drift while still requiring exact normalized home/away team names,
- reject ambiguous or out-of-tolerance odds mappings with explicit counters,
- add explicit unknown-starter placeholders for live games where one or both probable starters are unavailable,
- preserve strict historical/certification behavior; this is inference-only wrapper behavior.

## Acceptance

- 2026-08-13 local operator path no longer crashes when probable starters are partially missing.
- No invented starter identities or pitching stats are created.
- Focused tests cover tolerant odds matching, out-of-tolerance unmatched reporting, and unknown-starter placeholder behavior.
