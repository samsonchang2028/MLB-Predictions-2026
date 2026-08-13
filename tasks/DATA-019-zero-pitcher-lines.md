# DATA-019 - zero pitcher-line investigation

## Status

Completed.

## Scope

Make the DATA-018 all-zero pitcher-line failures auditable without mutating data or re-fetching MLB responses.

## Outcome

Added a read-only diagnostic script that queries local DuckDB game-detail attempts, joins schedule/game state and Silver pitcher appearance counts, and writes a JSON report summarizing affected games by season, error type, game state, and Silver coverage.

## Acceptance

- No network access required.
- DuckDB opened read-only.
- Report distinguishes DATA-016 guard rejection from the original projection defect.
- The 39 failed DATA-018 games remain visible rather than silently dropped.
