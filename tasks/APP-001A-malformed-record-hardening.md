# APP-001A - malformed prediction record hardening

## Status

Completed.

## Scope

Resolve the APP-001 deferred P2 where a malformed/stale prediction JSONL record could crash the whole daily board.

## Outcome

- `load_daily_board()` now skips malformed records instead of raising.
- `load_daily_board_with_diagnostics()` returns valid rows plus skipped-record reasons.
- Streamlit daily board displays a warning and expandable skipped-record table when malformed rows are present.
- Removed the xfail-pinned regression by converting it into passing tests.

## Tests

- `python.exe -m pytest tests\unit\app\test_board.py tests\unit\app\test_performance.py -q` -> 27 passed.
