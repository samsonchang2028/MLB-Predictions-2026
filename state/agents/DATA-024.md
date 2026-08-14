# DATA-024 agent status

| Field | Value |
|---|---|
| Task | DATA-024 |
| Role | implementer |
| Status | CANDIDATE |
| Branch | `agent/DATA-024-totals-odds-ingestion` |
| Worktree | `../predictions-1-wt-DATA-024` |
| Started | 2026-08-14 |
| Latest commit | (pending) |
| Tests | `pytest tests/unit/ingestion/odds/test_snapshots.py tests/unit/ingestion/odds/test_totals_snapshots.py -q` → **33 passed** |
| Blocker | none |

## Activity

Implemented sibling totals parser + bronze ingest parallel to moneyline path.

## Parser API

- `parse_the_odds_api_totals(payload, *, source="the_odds_api")` → list of snapshot dicts
- `ingest_the_odds_api_totals(connection, payload, *, source="the_odds_api")` → inserted row count

## Row shape (parse output / bronze `odds_totals_snapshots`)

| Field | Type | Notes |
|---|---|---|
| `source` | str | e.g. `the_odds_api` |
| `source_event_id` | str | Odds API event id |
| `bookmaker` | str | book key |
| `outcome` | str | `over` or `under` |
| `point` | float | total-runs line (e.g. 8.5) |
| `american_price` | int | American odds |
| `snapshot_timestamp` | datetime (UTC) | book `last_update` — ADR-002 timing field |
| `commence_time` | datetime (UTC) | event first pitch |
| `home_team` | str | for schedule mapping |
| `away_team` | str | for schedule mapping |

Bronze PK: `(source, source_event_id, bookmaker, outcome, point, snapshot_timestamp)` — allows multiple lines per book per snapshot.

## Silver gap (deferred)

`src/transforms/silver.py` still normalizes only `bronze.odds_moneyline_snapshots`. Totals land in new `bronze.odds_totals_snapshots`; no `silver.odds_totals` table or `game_pk` mapping yet. SIM-002 / daily operator need Silver mapping (reuse DATA-004 schedule join + DATA-009-style matching) before production consumption.

## Next gates

- Reviewer after CANDIDATE
- Tester after CANDIDATE
