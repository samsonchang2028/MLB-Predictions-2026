# OPS-001 Research — Daily Automation & Scheduling

Research only. No code/workflow changes made. Cross-references the other
in-flight feature research (Monte Carlo, Kalshi, weather, confirmed lineups,
totals, run lines, arbitrage scanner, live monitoring) for their scheduling
implications.

## 0. Starting point: what OPS-001 already says

`tasks/OPS-001-daily-operator-automation.md` is **not** a one-line backlog
stub — it's already a fleshed-out task file (deps `APP-004`, `PIPE-003`,
`OBS-002`; requirements, initial schedule hypothesis, acceptance criteria).
The one-line description in `tasks/index.md` is just the index summary. This
research assumes the existing task file's requirements and refines/extends
them rather than re-deriving from scratch. Key things it already commits to
that this doc treats as fixed:

- schedule defined in Pacific time with UTC cron equivalents,
- multiple daily prediction refreshes (starters/odds move during the day),
- result enrichment separately, tolerant of unfinished late games,
- secrets never committed,
- explicit decision on how CI gets the DB/artifacts,
- idempotent runs, no retraining on post-lock 2026 outcomes.

What it does **not** yet resolve — and what this doc focuses on — is *which*
artifact strategy to pick, concretely, with real cost numbers.

## 1. The core persistence problem

Confirmed on disk: `data/mlb.duckdb` is **1112.8 MB (~1.09 GB)**, plus
`data/raw/odds/historical_archive/...` (~76 MB) and MLB schedule JSON
(~19 MB across files). `data/` and `*.duckdb` are git-ignored
(`.gitignore` lines 11-12) — "reproducible from raw ingestion" per repo
convention, but the repo's own `state/CURRENT.md` describes the real
2021-2025 build as a "multi-hour ... live pull [that] is operator-run", so
"just rebuild it every run" is not viable for a scheduled job with any
reasonable time/API budget.

Since PIPE-005, `scripts/daily_predictions.py` calls
`refresh_pregame_game_details` → `invalidate_game_detail_payloads` +
`backfill_game_details(..., retry_unresolved=True)` + `normalize_silver`
**before** predicting — i.e. every run mutates bronze rows for today's
Preview/Live slate and rebuilds affected Silver tables. This is a genuine
read-modify-write cycle against the DB file, not a read-only query. Any
ephemeral runner (GitHub Actions VM, AWS Lambda, etc.) starts with an empty
filesystem each run, so this mutation is lost unless the DB is pulled down
before the run and the updated copy is pushed back up after.

A second, harder constraint that the prompt's framing didn't fully spell
out: **GitHub blocks any single pushed file over 100 MB outright** (files
over 50 MB just warn), and GitHub's own guidance is to keep repos under 1 GB,
strongly under 5 GB. So "commit `mlb.duckdb` to the repo instead of
`.gitignore`-ing it" isn't just against repo convention — it's not possible
without Git LFS (free tier: 1 GB storage / 1 GB bandwidth per month, then
$5/mo per 50 GB pack), and even with LFS, a 1+ GB binary diffed/pushed on
every scheduled run is a bad fit for git (no useful diffing on a binary,
history bloats forever, and LFS bandwidth is on the DB's full size each
push/pull, not the delta). This rules out "committed artifact" as the DB
strategy — it only remains viable for the small prediction JSON/JSONL
outputs the Streamlit app actually reads (`state/predictions/*.jsonl`,
low KB-MB range, which the repo already commits today).

That leaves two real shapes for the *database itself*:

**A. Object storage round-trip (works with any ephemeral runner, including
GitHub Actions).** Job downloads `data/mlb.duckdb` from a bucket at start,
runs `daily_predictions.py` (which mutates it), uploads the changed file back
at end, then separately commits/pushes only the small `state/predictions/*`
artifacts so Streamlit Cloud redeploys. Options and current (2026) pricing:

| Store | Storage cost | Egress cost | Notes |
|---|---|---|---|
| Backblaze B2 | $6/TB/mo (~$0.006/GB) | free up to 3x avg storage/mo, then $0.01/GB; free via Cloudflare | Cheapest; S3-compatible API works with `boto3`/`aws cli` |
| AWS S3 Standard | $0.023/GB/mo | $0.09/GB egress (first 10 TB) | ~4x B2 storage cost; egress adds up if pulling 1 GB twice daily |
| Google Cloud Storage | comparable to S3, ~$0.020/GB/mo | ~$0.12/GB egress | No pricing edge over B2/S3 for this use case |

At ~1.1 GB and 1-4 pulls/pushes a day, storage cost is under $0.01/month on
any of these; the real cost is the *download+upload time* (1+ GB over CI
bandwidth, easily 1-3 minutes each way) eating into GitHub Actions' free
2,000 min/month private-repo budget, and egress fees if not on B2/Cloudflare.
This is workable but adds real setup surface: a cloud account, a bucket, an
access key pair stored as a second secret family, and download/upload steps
wired into the workflow (with retry/failure handling if the round-trip
itself fails mid-run).

**B. Machine you already control with persistent local disk (self-hosted
runner / homelab box / small always-on VM).** The DB just lives on disk
between runs — no round-trip, no object-storage account, no egress cost, no
100 MB/1 GB git limits to work around. Cron (or systemd timer) runs
`daily_predictions.py` directly against the local file, and a final step
`git add state/predictions/*.jsonl && git commit && git push` (from that
same machine, using a GitHub credential/PAT) is what triggers Streamlit
Cloud's redeploy. This is structurally identical to what PIPE-002's "local
daily operator" already does today, just automated instead of manually
triggered by whoever runs it.

Given that this repo already has a documented local-operator pattern
(PIPE-002/PIPE-003/PIPE-005) and a genuinely large, actively-mutated DB, **B
is the lower-total-complexity option** unless there's a hard requirement for
zero local hardware dependency. A is the right call only if "no machine we
control must ever be on" is a real constraint.

## 2. Options comparison

| Option | Setup complexity | Ongoing cost | Reliability | New accounts/credentials |
|---|---|---|---|---|
| **GitHub Actions + object storage (B2/S3)** | High — workflow YAML, download/upload steps, bucket lifecycle, retry logic for a failed round-trip leaving a stale/partial DB | ~$0.01-0.10/mo storage + possible Actions minutes overage beyond 2,000 free min/mo (private repo) if runs are frequent/long | GitHub's own cron is **documented as unreliable in 2026** — community reports of 2-4hr typical delays and some workflows seeing 8-14hr delays or entirely dropped days, worse under platform load, with no built-in alerting on a missed run (must self-add a heartbeat check) | Object storage account (Backblaze/AWS/GCP) + access keys, in addition to GitHub (already used) and The Odds API (already used) |
| **Small rented cloud VM (Hetzner/DigitalOcean/Lightsail) + cron** | Medium — provision VM once, install Python/deps, clone repo, cron entry, systemd for restart-on-boot | Hetzner CX23 ~€5.49/mo (~$6), Lightsail from $3.50/mo, DigitalOcean droplet ~$24/mo for a size that comfortably fits a 1GB+ DuckDB workload — realistically $5-10/mo is enough for this job | You control the machine: failures are your own cron logs (no GitHub-side scheduling flakiness), but *you* now own the "did it actually run" monitoring — no built-in alerting either. VM/provider outages are rare but are a new failure class not present today | New hosting account (Hetzner/DO/AWS) + its billing; SSH key; still uses GitHub (push) and The Odds API (already used) |
| **Self-hosted homelab machine + cron/systemd** | Low-Medium if a machine is already on — same as VM setup minus provisioning, plus your home network/power reliability | $0 marginal (assuming the machine is already running for other reasons); real cost is your own uptime/power, not billed | Tied to your home internet/power — an outage silently skips the run with no external alerting unless you add one; but it's also the option operators already trust for the "multi-hour" historical rebuild described in `state/CURRENT.md` | No new accounts — reuses GitHub + The Odds API only, which fully matches "no new external accounts" |

Honest read: GitHub Actions' scheduled-workflow reliability problems in 2026
are a real, documented issue (not hypothetical) — for a job whose entire
point is running *before first pitch*, multi-hour unpredictable cron delay
is a correctness risk, not just an inconvenience, since PIPE-001's guard
already skips games after first pitch rather than silently backdating a
prediction. A self-hosted or rented-VM cron does not have this specific
failure mode (real OS cron is reliable to the minute); it trades that for
"you must monitor the machine yourself," which the homelab option was
already implicitly doing for the one-time historical rebuild.

## 3. Secrets handling (same principle regardless of platform)

`THE_ODDS_API_KEY` must never be committed — this repo already treats `.env`
as git-ignored and `daily_predictions.py` already reads it via
`os.environ.get("THE_ODDS_API_KEY")` (raises `RuntimeError` and refuses to
fetch live odds if unset and no `--odds-json` replay file given, so a
misconfigured secret fails loud, not silent).

- **GitHub Actions**: store as an Actions repo secret
  (`Settings → Secrets and variables → Actions`), inject as
  `env: THE_ODDS_API_KEY: ${{ secrets.THE_ODDS_API_KEY }}`. If using object
  storage (option A), the bucket access key/secret is a *second* secret pair
  stored the same way.
- **Rented VM**: a `.env` file outside the repo (or a systemd
  `EnvironmentFile=` pointed at a root-only-readable path) loaded before the
  cron invocation; never checked into the VM's clone of the repo.
- **Homelab machine**: same as VM — local `.env`/systemd `EnvironmentFile`,
  consistent with how a developer already runs this locally today.

Either way the credential boundary is identical: platform-native secret
store or an out-of-repo `.env`, matching the pattern already established for
local development. Nothing about picking A vs. B in section 1/2 changes this
answer for `THE_ODDS_API_KEY` itself — it only adds a *second* credential
(bucket keys) if object storage is chosen.

## 4. How each researched feature changes scheduling requirements

| Feature | Frequency impact | Why |
|---|---|---|
| Monte Carlo simulation | None | Pure modeling addition on top of the same once-daily feature/prediction build; no new data-freshness need. |
| Totals / run lines | None | Same — new markets predicted from the same daily feature build and the same once-daily (or multi-refresh) odds pull. |
| Weather | Minor, not a frequency change to the core job | Forecast accuracy improves closer to first pitch, so it wants to be fetched as *late* as practical within the existing "multiple refreshes during the day" pattern OPS-001 already plans — not a new cadence, just placement of the last pre-lock refresh. Historical actuals for training are a one-time backfill, irrelevant to the scheduled job. |
| Confirmed lineups | None (extends existing step) | Same "refresh right before prediction" shape PIPE-005 already built for probable starters (`refresh_pregame_game_details` → `invalidate_game_detail_payloads` + `backfill_game_details(retry_unresolved=True)` + `normalize_silver`). Lineup confirmation is additional data pulled in that same pregame-refresh step, no new scheduling infrastructure. |
| Kalshi integration | None | Mirrors The Odds API's existing once-(or few-times)-daily fetch cadence; same job, one more data source call. |
| **Arbitrage scanner** | **Materially different — needs polling every few minutes, not daily** | Arbitrage windows between books/exchanges close in minutes; a once- or few-times-daily job cannot catch them at all. This needs its own tight-loop job (or a persistent process), not an extension of `daily_predictions.py`'s cadence. Cost implication: polling The Odds API + Kalshi every 2-5 minutes across market hours is a large multiple of current API call volume — needs a rate-limit/cost check against each API's plan before committing to a frequency, and is a strong argument *for* the "already-on machine" (VM/homelab) option over GitHub Actions, since GitHub Actions' minimum schedule granularity is 5 minutes and is (per section 2) unreliable at exactly that granularity — burning through Actions minutes budget while also being the option with the worst timing precision for the one feature that most needs precise timing. |
| **Live monitoring (in-game)** | **Different shape — event-triggered/time-windowed, not a flat cron** | Needs frequent polling only *during* live games (roughly first pitch to final out for that day's slate), not all day. This argues for a scheduler that can start/stop around actual game windows (e.g. a daily job that computes today's game-time windows and spawns/kills a poller, or a systemd timer with a computed window) rather than one more fixed daily cron line. |

Net: the once-daily baseline job (predictions + result enrichment,
regardless of which of the other modeling features get added) has no
frequency pressure from Monte Carlo/totals/run-lines/weather/lineups/Kalshi
— those are all either pure modeling work or slot into refreshes OPS-001
already planned. Arbitrage and live monitoring are the two features that
actually break the "once daily, maybe a few refreshes" shape and justify
separate automation infrastructure with its own cost/reliability tradeoffs.

## 5. Proposed task breakdown

Recommend **splitting** rather than growing OPS-001 further: the baseline
daily cadence (predictions + result enrichment, still within reach of a few
scheduled refreshes/day) is a fundamentally different automation shape than
minute-scale polling for arbitrage/live monitoring, and bundling them would
force a single task to justify two very different platform choices at once.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| OPS-001 | backlog | APP-004, PIPE-003, OBS-002 | Baseline once-(to few-times)-daily automation: prediction refresh(es) before first pitch + result enrichment after games finish. Scope stays as currently written in `tasks/OPS-001-daily-operator-automation.md`; this research recommends the artifact-strategy decision (req. 5) resolve to **self-hosted/VM cron with local DB persistence** as the default V1 shape, with GitHub Actions + object storage documented as the fallback if "no machine we control" becomes a hard requirement. |
| OPS-003 | backlog | OPS-001, arbitrage scanner research, live monitoring research | High-frequency polling infrastructure: minute-scale arbitrage odds polling and game-time-windowed live-monitoring polling. Explicitly NOT an extension of OPS-001's cron cadence — needs its own scheduling shape (always-on process or computed time-window triggers), its own API rate-limit/cost budget check against The Odds API + Kalshi plan limits, and its own decision on whether it runs on the same machine as OPS-001 or a separate one. Should not start until the arbitrage scanner and live monitoring feature research/design lands, since the polling shape depends on what those features actually need. |

`tasks/index.md`'s "Current optional graph candidates" table would gain the
`OPS-003` row (or whatever number is free at implementation time) alongside
the existing `OPS-001` row, with `OPS-003`'s Depends-on including `OPS-001`
(reuses its secret/deploy pattern) plus the not-yet-existing arbitrage
scanner and live monitoring task IDs.
