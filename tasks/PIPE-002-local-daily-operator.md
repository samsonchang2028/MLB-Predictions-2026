# PIPE-002 - local daily operator

## Status

Completed.

## Role

Implementer.

## Scope

Package the existing PIPE-001 daily prediction pipeline into a real local operator script that:

- fits the ADR-006 locked V1 model from repaired 2021-2025 data only,
- fetches live MLB h2h moneylines from The Odds API using `THE_ODDS_API_KEY`,
- maps live odds to the local MLB `game_pk` slate,
- builds today's point-in-time Gold inference rows,
- appends immutable prediction records for the Streamlit daily board.

## Constraints

- Do not use mock data for production runs.
- Do not train on 2026 results without a new methodology decision.
- Preserve PIPE-001 timestamp guards: odds snapshot before prediction timestamp before first pitch.
