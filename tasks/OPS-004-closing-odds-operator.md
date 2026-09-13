# OPS-004 — Closing Odds Operator (MARKET-009 deployment)

## Status

ready

## Dependencies

- MARKET-009 (merged `ba72c86`: `scripts/closing_odds_capture.py`, `src/market/odds_closes.py`)
- OPS-001 (homelab systemd pattern: `deploy/systemd/`, `docs/homelab-operations.md`)
- PIPE-004 (`odds_books.jsonl` source for one-time backfill)

## Execution

Primary role: `implementer`

Review required: `yes`

Tester required: `yes`

Worktree required: `yes`

Branch: `agent/OPS-004-closing-odds-operator`

## Goal

Deploy MARKET-009 closing-odds capture on the homelab:

1. One-time retrospective backfill of `state/predictions/odds_closes.jsonl` (earliest anchor).
2. Recurring pre-first-pitch live capture every 10 minutes via systemd.
3. Runbook + unit tests so a maintainer can install and verify without reading MARKET-009 code.

## Read first

- `AGENTS.md`
- `state/CURRENT.md` (MARKET-009 entry)
- `state/task-context/MARKET-009.md`
- `scripts/closing_odds_capture.py`
- `deploy/systemd/mlb-predictions-daily.{service,timer}` (pattern)
- `docs/homelab-operations.md`
- `tests/unit/scripts/test_systemd_units.py`

## Allowed files

- `deploy/systemd/mlb-predictions-closing-odds.{service,timer}`
- `deploy/systemd/mlb-predictions-closing-odds-backfill.service` (optional one-shot)
- `docs/homelab-operations.md`
- `tests/unit/scripts/test_systemd_units.py`
- `tasks/OPS-004-closing-odds-operator.md`
- `tasks/index.md` (if writable)
- `state/task-context/OPS-004.md`
- `state/agents/OPS-004.md`
- `state/CURRENT.md`

## Do not modify

- `scripts/closing_odds_capture.py` logic (unless a minimal CLI flag is strictly required for systemd — prefer documenting existing flags)
- Production PLAY policy / ADR-007
- `state/predictions/*.jsonl` contents in-repo

## Requirements

1. **Backfill service (one-shot):** systemd oneshot that runs as `mlbpred`:
   ```bash
   .venv/bin/python scripts/closing_odds_capture.py --backfill-from-odds-books --anchor earliest
   ```
   Output: `state/predictions/odds_closes.jsonl`. Document expected row count (~112–113 on current host).

2. **Live capture timer:** invoke `scripts/closing_odds_capture.py` (no `--date`, default today) every **10 minutes** during the homelab day. Script already no-ops when no games are in the 60–15 minute pre-first-pitch window.

3. **Unit file conventions** (match OPS-001):
   - `User=mlbpred`, `Group=mlbpred`
   - `WorkingDirectory=/opt/mlb-predictions`
   - `EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env`
   - journald logging, no secrets in units
   - Do **not** acquire `operator.lock` (closing capture is read-only DuckDB + append-only `odds_closes.jsonl`; must not block daily predict/enrich)

4. **Runbook:** extend `docs/homelab-operations.md` with install, enable, backfill, verify, logs, and disable sections for closing odds.

5. **Tests:** extend `tests/unit/scripts/test_systemd_units.py` for new units (env file, journal, script path, timer cadence, no API key in unit).

6. **Verification commands** (document in handoff):
   ```bash
   wc -l state/predictions/odds_closes.jsonl
   sudo systemctl start mlb-predictions-closing-odds-backfill.service
   sudo systemctl enable --now mlb-predictions-closing-odds.timer
   journalctl -u mlb-predictions-closing-odds.service -n 50 --no-pager
   ```

## Acceptance criteria

- Committed systemd units install cleanly (`systemd-analyze verify` documented)
- Runbook tells operator exact sudo commands for backfill + timer enable
- Tests pass for new unit files
- No change to PLAY policy or CLV methodology

## Handoff

Record: summary, files changed, test commands, operator install steps (sudo required on host), expected backfill row count.
