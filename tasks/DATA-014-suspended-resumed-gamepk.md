# DATA-014 - Suspended/Resumed Same-Final game_pk Reconciliation

## Status

done (merged)

## Dependencies

- DATA-002 (schedule ingestion)
- DATA-013 (duplicate game_pk reconciliation)

## Origin

Surfaced by the DATA-011 real 2021 build after DATA-013. A **suspended-then-
resumed** game lists the same `game_pk` under both its original and resumption
dates, and BOTH entries are `Final` (same lifecycle stage), so DATA-013's
same-stage conflict guard fails it. Confirmed with real data, `game_pk` 632192
(2021):

- bucket 2021-04-11: abstract/detailed `Final`, coded `F`, officialDate
  2021-04-11, gameDate 2021-04-11, `resumeDate` 2021-08-31.
- bucket 2021-08-31: abstract/detailed `Final`, coded `F`, officialDate
  2021-04-11, gameDate 2021-08-31, `resumedFromDate` 2021-04-11.
- **Outcome is identical in both**: home 121 score 6 (win), away 146 score 5
  (loss).

The two entries differ only in scheduling/linkage metadata (gameDate,
resumeDate/resumedFromDate, calendarEventID, series counts). They are the same
completed game, so they must reconcile to one canonical row rather than FAIL.

## Requirements

- Same-`game_pk` entries at the same top lifecycle stage (both Final) whose
  outcome-identifying fields agree must reconcile to exactly one canonical row,
  not FAIL.
- Outcome-identifying fields = home/away team ids, home/away score, winner.
- A genuine outcome conflict (differing teams, scores, or winner at the same
  stage) must still FAIL (do not silently resolve).
- Preserve suspension/resume linkage metadata (resumeDate / resumedFromDate) and
  original/resumed dates on the canonical row where available.
- Produce exactly one canonical Silver game row per `game_pk`.
- Canonical selection among identical-outcome Final entries must be
  deterministic and documented.
- Preserve all DATA-013 behavior: postponed+rescheduled (different stages) still
  reconciles with Final precedence; distinct game_pks and doubleheaders stay
  separate; the equal-time conflict guard, immutability, and idempotency are
  unchanged.

## Allowed files

- `src/ingestion/mlb/schedule.py`
- `tests/unit/ingestion/mlb/`
- `tests/integration/ingestion/mlb/`

## Design notes

- The existing reconciliation already ranks Final above non-final and merges
  reschedule metadata; extend the same-top-stage case to compare only
  outcome-identifying fields for the conflict decision, and reconcile (not fail)
  when those agree.
- Document the point-in-time nuance for later FEAT tasks: a resumed game's result
  is only known on the resumption date (gameDate 2021-08-31) even though its
  officialDate is 2021-04-11. DATA-014 only fixes ingestion reconciliation; the
  temporal handling of resumed games in features is a separate downstream concern
  (record as a deferred follow-up, do not resolve here).

## Required tests

- Regression fixture for `game_pk` 632192: original (Final, resumeDate) +
  resumption (Final, resumedFromDate) with identical 6-5 outcome reconciles to
  one canonical game with preserved resume linkage metadata; `normalize_silver`
  yields exactly one `silver.games` row.
- Same-stage genuine outcome conflict (two Final entries, differing scores or
  winner) still FAILs.
- DATA-013 cases still hold: postponed+rescheduled reconciles; doubleheader (two
  distinct game_pks) stays separate; differing-teams duplicate FAILs.

## Merge-blocking conditions

- Any FAIL on a same-outcome suspended/resumed duplicate.
- Any silent resolution of a genuine outcome conflict (must FAIL).
- More than one canonical Silver row for a single `game_pk`.
- Any regression to DATA-013 reconciliation, doubleheader separation,
  equal-time conflict handling, immutability, or idempotency.

## Handoff

Record the refined conflict rule (outcome-field comparison), the deterministic
canonical-selection policy, tests added, commands run, results, and the deferred
point-in-time follow-up for resumed games.
