# Homelab daily automation plan

Status: research / proposed plan only. No service, timer, code entrypoint, or
production scheduler has been installed by this document.

## Goal

Run the MLB prediction system every day from a Linux homelab with minimal manual
work, while keeping the homelab as the owner of local data:

- `data/mlb.duckdb`
- local Parquet/raw artifacts
- model/evaluation reports
- `state/predictions/*.jsonl`
- Streamlit-readable prediction artifacts

Do not expose DuckDB as a shared multi-writer database over SMB/NFS. Other
clients should read outputs through Streamlit or a future read-only API.

## Current repo capabilities

The repo already has most of the operational pieces:

- `scripts/daily_predictions.py`
  - trains/loads the ADR-006 locked daily model path from local data,
  - refreshes current game-detail payloads unless `--skip-detail-refresh`,
  - fetches The Odds API live odds using `THE_ODDS_API_KEY`,
  - builds point-in-time-safe daily inference rows,
  - writes append-only predictions to `state/predictions/daily.jsonl`,
  - writes feature and odds-detail artifacts,
  - optionally writes simulation artifacts with `--enable-simulation`,
  - skips games that violate `prediction_timestamp < first_pitch`,
  - returns non-zero on real command failure.
- `scripts/enrich_prediction_results.py`
  - enriches already-written prediction rows with completed results,
  - writes append-only rows to `state/predictions/journal.jsonl`,
  - tolerates unfinished games by skipping them,
  - is idempotent for already-enriched results.
- `src/pipelines/daily.py`
  - owns immutable prediction-record semantics,
  - prevents duplicate/conflicting prediction records,
  - enforces pregame odds and first-pitch guards.

The missing operational layer is a stable Linux run wrapper plus systemd
service/timer files and a runbook.

## Recommended production shape

Use **systemd service + timer** on the homelab.

Reasons:

- native Linux service supervision,
- logs visible with `journalctl`,
- non-zero exits are visible in `systemctl status`,
- timers can run multiple times per day,
- no new orchestration platform,
- better operational fit than GitHub Actions because DuckDB stays local and is
  too large / stateful for ephemeral CI runners.

Do not add Airflow, Prefect, Dagster, Kubernetes, Celery, Kafka, or similar
systems for V1 operations.

## Proposed stable command

Today, the direct production command is:

```bash
python scripts/daily_predictions.py --date YYYY-MM-DD
```

Recommended implementation task: add a thin homelab wrapper command that runs
the daily workflow stages and owns logging/stage labels:

```bash
python scripts/run_daily_operator.py --date YYYY-MM-DD
```

That wrapper should call the existing scripts rather than duplicate their logic:

1. prediction refresh:

   ```bash
   python scripts/daily_predictions.py --date "$RUN_DATE" --enable-simulation
   ```

2. result enrichment:

   ```bash
   python scripts/enrich_prediction_results.py --date "$RUN_DATE"
   ```

The scheduler should eventually invoke the wrapper, not know the internal
sequence. Until that wrapper exists, systemd can call the two existing scripts
as separate services/timers.

## Environment and secrets

Recommended homelab env file:

```text
/etc/mlb-predictions/mlb-predictions.env
```

Expected variables:

```bash
THE_ODDS_API_KEY=...
PYTHONUNBUFFERED=1
TZ=America/Los_Angeles
```

Optional path overrides if the repo is not deployed at the default location:

```bash
MLB_PREDICTIONS_ROOT=/opt/mlb-predictions
PREDICTIONS_STORE_PATH=state/predictions/daily.jsonl
PREDICTION_JOURNAL_PATH=state/predictions/journal.jsonl
SKIPPED_STORE_PATH=state/predictions/skipped.jsonl
```

Do not commit this file. Do not put API keys into systemd unit files directly.

## Suggested homelab layout

```text
/opt/mlb-predictions/
  .venv/
  data/mlb.duckdb
  reports/
  state/predictions/
  streamlit_app.py
  scripts/
  src/
```

The Linux service user should own this directory, for example:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin mlbpred
sudo chown -R mlbpred:mlbpred /opt/mlb-predictions
```

## Proposed systemd services

### Prediction service

Candidate file:

```text
/etc/systemd/system/mlb-predictions-daily.service
```

Proposed contents:

```ini
[Unit]
Description=MLB daily prediction operator
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=mlbpred
Group=mlbpred
WorkingDirectory=/opt/mlb-predictions
EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env
ExecStart=/opt/mlb-predictions/.venv/bin/python scripts/daily_predictions.py --enable-simulation
StandardOutput=journal
StandardError=journal
```

### Result-enrichment service

Candidate file:

```text
/etc/systemd/system/mlb-predictions-enrich.service
```

Proposed contents:

```ini
[Unit]
Description=MLB daily prediction result enrichment
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=mlbpred
Group=mlbpred
WorkingDirectory=/opt/mlb-predictions
EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env
ExecStart=/opt/mlb-predictions/.venv/bin/python scripts/enrich_prediction_results.py
StandardOutput=journal
StandardError=journal
```

Note: the current scripts default to today's local date when `--date` is omitted.
Manual reruns should pass `--date YYYY-MM-DD` explicitly.

## Proposed timers

The main practical issue is MLB game timing. Runs must happen before first pitch.
Because slates vary, use multiple prediction refreshes. Late runs are safe
because the existing pipeline skips already-started games.

### Prediction timer

Candidate file:

```text
/etc/systemd/system/mlb-predictions-daily.timer
```

Pacific schedule:

- 07:30 PT: morning odds/starter refresh
- 10:30 PT: before common early East Coast first pitches
- 13:30 PT: afternoon refresh
- 16:00 PT: before common evening windows
- 18:00 PT: West Coast/night refresh

Systemd can express local times directly if the host timezone is
`America/Los_Angeles`:

```ini
[Unit]
Description=Run MLB daily predictions several times before first pitch windows

[Timer]
OnCalendar=*-*-* 07:30:00
OnCalendar=*-*-* 10:30:00
OnCalendar=*-*-* 13:30:00
OnCalendar=*-*-* 16:00:00
OnCalendar=*-*-* 18:00:00
Persistent=true
Unit=mlb-predictions-daily.service

[Install]
WantedBy=timers.target
```

UTC equivalents during Pacific Daylight Time:

- 07:30 PT = 14:30 UTC
- 10:30 PT = 17:30 UTC
- 13:30 PT = 20:30 UTC
- 16:00 PT = 23:00 UTC
- 18:00 PT = 01:00 UTC next day

UTC equivalents during Pacific Standard Time are one hour later.

Recommendation: keep the homelab timezone as `America/Los_Angeles` and use
local `OnCalendar` times to avoid DST mistakes.

### Result-enrichment timer

Candidate file:

```text
/etc/systemd/system/mlb-predictions-enrich.timer
```

Pacific schedule:

- 22:45 PT: most East/Central games complete
- 00:45 PT: West Coast / delayed game catch-up
- 07:15 PT: next-morning catch-up

```ini
[Unit]
Description=Refresh MLB prediction result journal after games finish

[Timer]
OnCalendar=*-*-* 22:45:00
OnCalendar=*-*-* 00:45:00
OnCalendar=*-*-* 07:15:00
Persistent=true
Unit=mlb-predictions-enrich.service

[Install]
WantedBy=timers.target
```

Unfinished games are skipped by the enrichment operator and can be picked up on
the next timer run.

## Install / enable commands

Proposed homelab setup:

```bash
cd /opt/mlb-predictions
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Install services after implementation adds `deploy/systemd/` files:

```bash
sudo cp deploy/systemd/mlb-predictions-daily.service /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-daily.timer /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-enrich.service /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-enrich.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mlb-predictions-daily.timer
sudo systemctl enable --now mlb-predictions-enrich.timer
```

This document does not create `deploy/systemd/` yet. That should be part of the
implementation task once this plan is approved.

## Manual operations

Run today's predictions:

```bash
cd /opt/mlb-predictions
THE_ODDS_API_KEY=... .venv/bin/python scripts/daily_predictions.py --enable-simulation
```

Run a specific date:

```bash
cd /opt/mlb-predictions
THE_ODDS_API_KEY=... .venv/bin/python scripts/daily_predictions.py --date 2026-08-15 --enable-simulation
```

Enrich today's completed results:

```bash
cd /opt/mlb-predictions
.venv/bin/python scripts/enrich_prediction_results.py
```

Enrich a specific date:

```bash
cd /opt/mlb-predictions
.venv/bin/python scripts/enrich_prediction_results.py --date 2026-08-15
```

Inspect service logs:

```bash
journalctl -u mlb-predictions-daily.service -n 200 --no-pager
journalctl -u mlb-predictions-enrich.service -n 200 --no-pager
```

Follow logs live:

```bash
journalctl -u mlb-predictions-daily.service -f
```

Check timers:

```bash
systemctl list-timers 'mlb-predictions*'
systemctl status mlb-predictions-daily.timer
systemctl status mlb-predictions-enrich.timer
```

Disable automation:

```bash
sudo systemctl disable --now mlb-predictions-daily.timer
sudo systemctl disable --now mlb-predictions-enrich.timer
```

## Idempotency behavior

Prediction records are append-only and keyed by `(game_pk, prediction_timestamp)`.

Expected behavior:

- same command with same explicit `--prediction-timestamp` and same inputs:
  duplicate write is skipped,
- same command later with a new timestamp:
  a new prediction snapshot may be appended for games still before first pitch,
- same command after first pitch:
  affected games are skipped with `prediction_not_before_first_pitch`,
- conflicting rewrite for an existing key:
  command fails instead of mutating the prior record.

The Streamlit board collapses same-game reruns to the latest displayed prediction
per `game_pk`, so multiple pregame refreshes are acceptable.

Result enrichment is append-only and idempotent for already observed completed
results. Re-running enrichment for a date should not mutate original prediction
records.

## Logging / failure visibility

Current scripts already emit bracketed stage logs such as:

- `[gate]`
- `[model]`
- `[load]`
- `[slate]`
- `[predictions]`
- `[skips]`
- `[error]`

Systemd captures stdout/stderr into journald. A real command failure should
return non-zero and show in:

```bash
systemctl status mlb-predictions-daily.service
journalctl -u mlb-predictions-daily.service
```

Recommended implementation improvement: add a tiny wrapper that prints:

- `run_id`,
- `run_date`,
- `stage`,
- start/end timestamps,
- child command exit code.

Do not build a full observability platform for this.

## Proposed implementation tasks

These are proposed tasks only. They are not scheduled by this document.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| OPS-001A | backlog | OPS-001, PIPE-005, OBS-002 | add a homelab daily operator wrapper that runs prediction and enrichment stages with clear stage logs and non-zero failure propagation |
| OPS-001B | backlog | OPS-001A | add `deploy/systemd/*.service` and `*.timer` files for prediction refreshes and result enrichment |
| OPS-001C | backlog | OPS-001B | add focused tests for wrapper idempotency assumptions, failure propagation, and stage logging using fake subprocess calls / fixtures; no live MLB API |
| OPS-001D | backlog | OPS-001B | update README or deployment docs with homelab installation, environment, rerun, logs, and disable instructions |

## Acceptance criteria for implementation

When implemented, the homelab automation task should prove:

- one stable command can run the full daily operator path,
- systemd timers can run that command automatically,
- reruns do not duplicate predictions incorrectly,
- games after first pitch are skipped, not predicted,
- result enrichment writes journal rows without mutating predictions,
- failures return non-zero and are visible in `journalctl`,
- secrets stay outside the repo,
- Streamlit can continue reading existing `state/predictions/*.jsonl` artifacts.

## Remaining operational risks

- The current script trains the locked model during each daily run. This is
  acceptable for a homelab if runtime is tolerable, but a future optimization
  could persist a model artifact after a separate ADR/task.
- Multiple prediction refreshes create multiple pregame snapshots. This is good
  operationally, but UI/result reporting must continue to be explicit about
  which snapshot is displayed.
- The Odds API outages or rate limits will fail/skip live odds. Logs must make
  this visible.
- MLB probable-starter timing varies by team/day. Multiple pregame refreshes are
  necessary; the app should continue surfacing games waiting on starters/odds.
- 2026/post-lock result data must remain operational monitoring only unless a
  new accepted methodology ADR says otherwise.

