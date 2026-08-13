# DATA-020 - inactive zero-line pitcher handling

## Status

Completed.

## Scope

Repair the overly strict DATA-016 hollow-payload guard that rejected completed games when MLB listed an inactive pitcher with a complete 0 IP / 0 BF / 0 pitch line even though another pitcher on the same team had real activity.

## Outcome

- The game-detail guard now rejects a completed side only when no listed pitcher has recorded activity.
- Listed zero-activity pitchers are allowed when a same-side pitcher has real pitching activity.
- Silver normalization skips inactive zero-activity pitcher rows and treats the first active pitcher as the actual starter.

## Constraints

- No network re-fetch was performed in this task.
- No DuckDB mutation was performed in this task.
- The existing hollow-payload protection remains: a completed side with no active pitchers still fails.

## Tests

- `python.exe -m pytest tests\unit\ingestion\mlb\test_game_detail.py tests\unit\transforms\test_pitcher_details.py -q` -> 48 passed.
