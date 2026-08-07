# DATA-013 - Season Schedule Duplicate game_pk Reconciliation

## Status

ready

## Dependencies

- DATA-002 (schedule ingestion)

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Origin

Surfaced by the DATA-011 real 2021-2025 build. A season-wide MLB schedule
response repeats a `game_pk` when a postponed game and its rescheduled makeup are
listed under both dates. Confirmed with real data: `game_pk` 634627 appears twice
in the 2021 season response:

- `2021-04-01` detailedState `Postponed` (coded `D`, `rescheduleDate` 2021-04-02)
- `2021-04-02` detailedState `Final`     (coded `F`, `rescheduledFromDate` 2021-04-01)

81 distinct game_pks are duplicated in 2021 alone. The current DATA-002 parser
(`_parse_games`) raises `duplicate gamePk ... within one schedule response`,
which aborts the whole build.

## Requirements

- Season schedule parsing may encounter repeated `game_pk`.
- Repeated IDs must be compared/reconciled, not blindly dropped.
- Preserve original/rescheduled dates or status history where available.
- Produce exactly one canonical Silver game row per `game_pk`.
- Final/played state takes precedence for outcome fields.
- Postponement metadata remains available.
- Conflicting duplicates must FAIL rather than be silently resolved.
- Add a regression fixture for `game_pk` 634627.
- Add at least one true doubleheader fixture proving two distinct `game_pk`s stay
  separate.

## Allowed files

- `src/ingestion/mlb/schedule.py`
- `tests/unit/ingestion/mlb/`
- `tests/integration/ingestion/mlb/`

## Design notes / constraints

- A `game_pk` denotes one game/matchup; differing teams across repeats is a real
  identity conflict and must FAIL (do not silently resolve).
- The observation table is keyed `(game_pk, payload_sha256, observed_at)`, so a
  single response can retain only one observation per `game_pk`; repeats within a
  response must be reconciled to one canonical entry before insert. Cross-fetch
  status history (different `observed_at`) must continue to work unchanged.
- The played/Final entry already carries `rescheduledFromDate` (original date);
  keep that metadata on the canonical row.
- Preserve DATA-002's existing behavior for distinct game_pks, doubleheaders,
  the equal-time conflict guard, immutability, and idempotency.
- The existing unit test asserting "duplicate gamePk in one response is a
  cardinality error" encodes the now-incorrect assumption and must be updated to
  the reconciliation semantics, with the reason documented.

## Required tests

- Regression: a season-style payload repeating `game_pk` 634627 (Postponed +
  rescheduled Final across two dates) reconciles to one canonical game with the
  played/Final outcome state and preserved reschedule metadata; `normalize_silver`
  yields exactly one `silver.games` row for it.
- A true doubleheader (two distinct `game_pk`s, same date, gameNumber 1 & 2)
  stays two separate rows through Bronze and Silver.
- Conflicting duplicates FAIL: differing teams for one `game_pk`, and two
  differing entries at the same played/Final lifecycle level.
- Benign identical repeats reconcile to one without error.

## Merge-blocking conditions

- Any blind drop of a repeated `game_pk` without reconciliation.
- Any silent resolution of a genuinely conflicting duplicate (must FAIL).
- More than one canonical Silver row for a single `game_pk`.
- Any regression to doubleheader separation, equal-time conflict handling,
  immutability, or idempotency.

## Handoff

Record the reconciliation rule, precedence policy, conflict conditions, the
updated/added tests, commands run, and results.
