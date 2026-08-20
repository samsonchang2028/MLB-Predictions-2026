# Homelab daily operations

This is the production runbook for the Linux homelab that owns the MLB
predictor's DuckDB, raw/Parquet data, model inputs, and prediction artifacts.

## Operational contract

The stable application entrypoint is:

```bash
.venv/bin/python scripts/run_daily_operator.py --stage all
```

The wrapper owns the internal sequence:

1. ingest the requested day's MLB schedule into Bronze;
2. normalize Silver so the current slate is visible;
3. run the existing point-in-time-safe prediction operator;
4. run the existing append-only result-enrichment operator.

Systemd uses the same entrypoint with `--stage predict` before games and
`--stage enrich` after games. The scheduler does not encode internal ingestion,
feature, prediction, odds, or journal steps.

The homelab is the sole owner and writer of:

- `/opt/mlb-predictions/data/mlb.duckdb`
- `/opt/mlb-predictions/data/raw/` and local Parquet files
- `/opt/mlb-predictions/state/predictions/*.jsonl`
- local model/evaluation artifacts

Do not expose DuckDB over SMB or NFS as a shared multi-writer database.
Streamlit should run on the homelab and read the JSON artifacts, or another
machine should consume a future read-only API/published artifact copy.

## Host and repository setup

The units assume this layout and service account:

```text
/opt/mlb-predictions/
  .venv/
  data/mlb.duckdb
  deploy/systemd/
  scripts/
  src/
  state/predictions/
```

Create the account and install the repository:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin mlbpred
sudo mkdir -p /opt/mlb-predictions /etc/mlb-predictions
sudo chown -R mlbpred:mlbpred /opt/mlb-predictions
sudo -u mlbpred git clone YOUR_REPOSITORY_URL /opt/mlb-predictions
cd /opt/mlb-predictions
sudo -u mlbpred python3 -m venv .venv
sudo -u mlbpred .venv/bin/python -m pip install --upgrade pip
sudo -u mlbpred .venv/bin/python -m pip install -r requirements.txt
```

The host needs Python 3.11+, Git, and `flock` from `util-linux`. Keep adequate
local disk space for DuckDB, raw MLB payloads, and regenerated Silver data.

Copy or restore the existing local `data/`, required certification artifact,
and any desired prediction history into `/opt/mlb-predictions`. Ensure all
files remain owned by `mlbpred:mlbpred`.

## Secrets and environment

Create `/etc/mlb-predictions/mlb-predictions.env`:

```text
THE_ODDS_API_KEY=replace_with_real_key
PYTHONUNBUFFERED=1
TZ=America/Los_Angeles
```

Protect it:

```bash
sudo chown root:mlbpred /etc/mlb-predictions/mlb-predictions.env
sudo chmod 0640 /etc/mlb-predictions/mlb-predictions.env
```

Never commit this file or place the API key in a unit file. The committed units
load it through `EnvironmentFile=`.

## Install and start systemd automation

```bash
cd /opt/mlb-predictions
sudo cp deploy/systemd/mlb-predictions-daily.service /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-daily.timer /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-enrich.service /etc/systemd/system/
sudo cp deploy/systemd/mlb-predictions-enrich.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify \
  /etc/systemd/system/mlb-predictions-daily.service \
  /etc/systemd/system/mlb-predictions-daily.timer \
  /etc/systemd/system/mlb-predictions-enrich.service \
  /etc/systemd/system/mlb-predictions-enrich.timer
sudo systemctl enable --now mlb-predictions-daily.timer
sudo systemctl enable --now mlb-predictions-enrich.timer
```

Check the next scheduled runs:

```bash
systemctl list-timers 'mlb-predictions-*'
```

## Schedule

The timers declare `America/Los_Angeles` explicitly, so daylight-saving changes
do not require editing UTC cron expressions.

Prediction refreshes run at 07:30, 10:30, 13:30, 16:00, and 18:00 Pacific.
During PDT these are 14:30, 17:30, 20:30, 23:00, and 01:00 UTC; during PST they
are one hour later. A refresh after a game's first pitch remains safe: the
existing daily pipeline skips that game rather than generating a late prediction.

Result enrichment runs at 22:45, 00:45, and 07:15 Pacific. Unfinished, delayed,
or extra-inning games are skipped and picked up by a later pass.

Both services acquire `/opt/mlb-predictions/data/operator.lock` before touching
local state. The lock lives beside the homelab-owned database in the already
ignored `data/` directory. This serializes DuckDB writers and waits up to 30
minutes for another operator invocation to finish.

## Manual execution

Load the protected environment when running as the service account:

```bash
cd /opt/mlb-predictions
sudo -u mlbpred bash -c \
  'set -a; source /etc/mlb-predictions/mlb-predictions.env; set +a; .venv/bin/python scripts/run_daily_operator.py --stage all'
```

Run only today's pregame refresh:

```bash
sudo systemctl start mlb-predictions-daily.service
```

Run only result enrichment:

```bash
sudo systemctl start mlb-predictions-enrich.service
```

Run or repair a specific MLB date:

```bash
sudo -u mlbpred bash -c \
  'set -a; source /etc/mlb-predictions/mlb-predictions.env; set +a; cd /opt/mlb-predictions && .venv/bin/python scripts/run_daily_operator.py --stage all --date 2026-08-20 --run-id manual-2026-08-20'
```

For an exactly repeatable prediction rerun, reuse an explicit pre-first-pitch
timestamp:

```bash
.venv/bin/python scripts/run_daily_operator.py \
  --stage predict \
  --date 2026-08-20 \
  --prediction-timestamp 2026-08-20T15:30:00+00:00
```

The existing prediction store suppresses an identical record at the same
`(game_pk, prediction_timestamp)` and rejects conflicting rewrites. A later
scheduled invocation intentionally creates a new pregame snapshot. Games whose
first pitch has passed are skipped. Result enrichment is append-only and
idempotent for an already-observed result.

## Logs and failure recovery

Each wrapper line includes UTC timestamp, run ID, MLB date, stage, status, and
child exit code. A real stage failure stops downstream stages and makes the
systemd service fail non-zero.

```bash
systemctl status mlb-predictions-daily.service
systemctl status mlb-predictions-enrich.service
journalctl -u mlb-predictions-daily.service -n 200 --no-pager
journalctl -u mlb-predictions-enrich.service -n 200 --no-pager
journalctl -u mlb-predictions-daily.service -f
```

After correcting the cause—commonly network access, API credentials, local disk
space, or file ownership—rerun the failed date with the manual command above.
The underlying ingestion, prediction, and journal stores are restartable and
idempotent; do not delete DuckDB or prediction artifacts to recover a run.

## Streamlit on the homelab

Streamlit continues reading the existing JSON/report artifacts:

```bash
cd /opt/mlb-predictions
sudo -u mlbpred .venv/bin/streamlit run streamlit_app.py \
  --server.address 0.0.0.0 \
  --server.port 8501
```

This runbook does not expose DuckDB to remote clients or publish private data to
GitHub. Reverse-proxy/TLS configuration is host-specific and outside OPS-001.

## Disable automation

```bash
sudo systemctl disable --now mlb-predictions-daily.timer
sudo systemctl disable --now mlb-predictions-enrich.timer
```

The repository, database, and artifacts remain intact. To remove only the unit
installation later, disable the timers first, remove the four files from
`/etc/systemd/system/`, and run `sudo systemctl daemon-reload`.
