# OPS-001 — scheduled daily operator automation

## Status

candidate (OPS-001A-D implemented; awaiting reviewer/tester)

## Dependencies

- APP-004
- PIPE-003
- OBS-002

## Execution

Primary role: implementer

Review required: yes

Tester required: yes

Worktree required: yes

## Goal

Automate the daily prediction and result-enrichment workflow without weakening
point-in-time prediction guarantees.

## Read first

- `AGENTS.md`
- `state/CURRENT.md`
- `tasks/PIPE-002-local-daily-operator.md`
- `tasks/PIPE-003-live-operator-hardening.md`
- `tasks/PIPE-005-pregame-detail-refresh.md`
- `tasks/OBS-002-result-enrichment-operator.md`
- `tasks/APP-004-streamlit-deployment.md`
- `docs/decisions/ADR-002-point-in-time.md`

## Allowed files

- `.github/workflows/`
- `scripts/`
- `docs/`
- `README.md`
- `tasks/OPS-001-daily-operator-automation.md`
- `tasks/index.md`
- `state/CURRENT.md`

## May modify if necessary

- app copy that tells users when data was last refreshed

## Do not modify

- model methodology / ADR-006
- prediction timestamp guards
- final holdout policy

## Inputs

- `THE_ODDS_API_KEY` as an environment secret
- local or CI-accessible DuckDB/data artifact strategy
- daily prediction outputs under `state/predictions/`
- OBS-002 result enrichment outputs

## Outputs

- documented automation plan and/or GitHub Actions workflow
- clear operator schedule in Pacific time
- explicit artifact strategy for Streamlit deployment

## Requirements

1. Define the automation schedule in Pacific time and explain its UTC cron
   equivalents.
2. Prediction runs must occur before first pitch for each game being predicted.
   A run after first pitch must skip that game via the existing guard.
3. Result enrichment runs must occur after games finish and must tolerate
   unfinished late games.
4. Do not put API keys in the repo. Use GitHub Actions secrets or local `.env`
   loading only.
5. Decide and document how CI gets the database/artifacts Streamlit needs:
   committed artifacts, release artifact, external object storage, or local-only
   operator.
6. Automation should run multiple daily prediction refreshes when useful because
   probable starters and odds move throughout the day.
7. Automation must not inspect or retrain on post-lock 2026 outcomes unless a
   new accepted methodology policy allows it.

## Initial schedule hypothesis

This must be validated by research before implementation, but the likely V1
shape is:

- morning prediction refresh after probable-starter/odds data is available,
- midday/afternoon refresh before common first-pitch windows,
- early-evening refresh for West Coast/night games,
- late-night result enrichment pass,
- next-morning result enrichment catch-up for extra innings, delays, or late
  West Coast games.

## Critical correctness constraints

- Every prediction row must keep `prediction_timestamp < game_start_timestamp`.
- Odds snapshots must precede prediction timestamp.
- Result enrichment is post-game observability and must not change predictions.
- Scheduled runs must be idempotent.

## Acceptance criteria

- A maintainer can configure secrets and understand exactly when jobs run.
- Automation either ships as a working workflow or documents why local-only is
  still required.
- The workflow/operator logs show predictions written/skipped and results
  enriched/skipped.
- Streamlit users can see when displayed artifacts were generated.

## Required tests

- workflow/script smoke where practical
- tests for any new scheduling helper logic
- no test may bypass existing pregame guards

## Handoff

Record:

- summary,
- files changed,
- commands run,
- test results,
- secret/artifact setup needed,
- recommended production schedule.

## Candidate handoff

- OPS-001A: added `scripts/run_daily_operator.py`, the stable application
  entrypoint. It refreshes the target-day schedule, normalizes Silver, invokes
  the existing prediction operator, and invokes append-only result enrichment.
  `--stage predict|enrich|all` supports scheduled and manual operation.
- OPS-001B: added two oneshot services and two persistent timers under
  `deploy/systemd/`. They use explicit `America/Los_Angeles` schedules,
  journald, an external environment file, and a shared `flock` lock under the
  homelab-owned `data/` directory so DuckDB writers cannot overlap.
- OPS-001C: added offline wrapper and unit-file tests covering stage order,
  timestamped/run-keyed logs, failure propagation, downstream stop behavior,
  stable explicit-timestamp reruns, secret isolation, timer cadence, and the
  absence of a first-pitch bypass.
- OPS-001D: added `docs/homelab-operations.md` with environment, secrets, data
  paths, installation, manual execution, failed-day rerun, logs, Streamlit,
  and disable instructions; linked it from README.
- Exact full-workflow command:
  `python scripts/run_daily_operator.py --stage all`.
- Required secret: `THE_ODDS_API_KEY` in
  `/etc/mlb-predictions/mlb-predictions.env`; no secret is committed.
- Prediction refresh schedule (Pacific): 07:30, 10:30, 13:30, 16:00, 18:00.
  Result enrichment: 22:45, 00:45, 07:15.
- Rerun behavior remains owned by the existing append-only stores: identical
  explicit prediction timestamps deduplicate, later pregame runs create a new
  snapshot, post-first-pitch games skip, result enrichment is idempotent, and
  conflicting rewrites fail.
- Failure behavior: every wrapper stage logs UTC timestamp, run ID, run date,
  status, and exit code; a failed stage returns non-zero and blocks downstream
  stages. Systemd exposes output through `journalctl`.
- Checks:
  - focused operator/daily/journal suite: 72 passed;
  - leakage suite: 41 passed;
  - full suite: 938 passed, 6 xfailed, 1 third-party deprecation warning;
  - `python scripts/run_daily_operator.py --help` passed;
  - `python -m compileall -q scripts/run_daily_operator.py` passed;
  - `git diff --check` passed.
- No live API smoke or Linux `systemd-analyze verify` was possible in this
  Windows implementation environment; both are documented homelab install
  checks.
- Merged to `main` (`add07cf`). Tester gate: **PASS** (2026-08-21; 56 focused
  operator/journal + 947 full suite). Reviewer gate: pending.
