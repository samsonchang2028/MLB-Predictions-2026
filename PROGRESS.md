# Progress Journal

A human-readable log of what we built, measured, and learned — written for
GitHub and for future-us. Technical detail lives in `state/CURRENT.md` and task
files; this journal is the story.

> **GitHub:** copy to repo root for visibility:
> `sudo -u mlbpred cp tasks/PROGRESS.md PROGRESS.md`

---

# Last three weeks (Aug 23 – Sep 13, 2026)

## Where we started

By late August the V1 stack was already running in production on the homelab:
daily predictions, result enrichment, Streamlit board, and a locked XGBoost
model (ADR-006). The open question was no longer “can we predict games?” but
**“should we trust the PLAY layer — the idea that big edges mean good bets?”**

That question drove almost everything in the last three weeks.

---

## Week 1 (~Aug 25 – Sep 1): See the model clearly, experiment in shadow

### Model monitoring (APP-014, Sep 3)

We added a **professor-readable monitoring tab** on Streamlit so you can inspect
model behavior without digging through JSONL files. The goal was transparency:
show probability quality, calibration, and live vs holdout context in plain
language.

### Shadow challengers (MARKET-004 & MARKET-005, Sep 3–6)

Instead of changing production PLAY rules, we built **read-only shadow strategies**
on the dashboard:

- **Uncertainty-adjusted challenger** — only “PLAY” when the model is confident
  *and* historically reliable in similar spots.
- **Consensus-confirmed challenger** — only PLAY when model and market agree.

Both preserve the legacy **2% edge threshold** as the baseline display. Nothing
here promotes a new betting policy; it’s instrumentation so we can compare
“what if we picked differently?” side by side.

**Takeaway:** We can now *see* alternatives without breaking production.

---

## Week 2 (~Sep 2 – Sep 8): The PLAY layer gets a verdict

### Documentation pass (DOCS-001, Sep 8)

README and docs were updated to reflect the new dashboard tabs, shadow
challengers, and monitoring — so GitHub visitors aren’t reading stale V1 copy.

### The big study (MARKET-006 & MARKET-007, Sep 9, PR #2)

We ran a formal **PLAY policy study** over the production journal: 135 candidate
rules, walk-forward gates, overfitting guards.

**Verdict: NO POLICY READY FOR PRODUCTION.**

Hard numbers on baseline PLAY (|edge| ≥ 2%):

| Metric | Value |
|--------|-------|
| Win rate | ~40% |
| ROI | ~−14.5% |
| Large-disagreement subset | ~35% win rate |
| Crossover subset (bet against model favorite) | ~34% win rate |

MARKET-007 shipped a **shadow dashboard** on the daily board — baseline PLAY
plus exploratory shadows, all labeled **NOT PRODUCTION**. The 2% threshold
stays display-only.

### Live diagnostic (ML-015, same period)

Separately we diagnosed the **first ~94 real production games**:

- **Model quality:** ROC-AUC essentially unchanged vs 2026 holdout (−0.002).
  The probability model is **not broken**.
- **PLAY quality:** 44% win rate on PLAY rows vs **57%** if you had simply taken
  the raw model favorite on the same games.
- **Mechanism:** The gap lives in **crossover games** — when edge sign flips the
  bet to the *opposite* of the model’s favorite. On those 27 games, edge-side
  won 9; raw favorite won 18 (McNemar p ≈ 0.12 — suggestive, not proven).

**Takeaway:** MODEL layer healthy. MARKET/PLAY layer concerning. Edge-based side
selection may be hurting more than helping.

### Homelab fix (OPS-001, Sep 9)

Enrich service timeout was fixed so late-night result enrichment doesn’t hang on
long-running games.

---

## Week 3 (~Sep 9 – Sep 13): Closing lines, CLV, and live capture

### CLV validation (MARKET-008, Sep 9)

**Closing Line Value** asks: did the market move toward your pick before first
pitch? It’s the standard sharp-bettor test — even more important than short-term
win rate.

First run on existing data:

| Finding | Value |
|---------|-------|
| Strict CLV sample | **8 games** (need ≥30) |
| Verdict | **CLV DATA INSUFFICIENT** |
| Most odds snapshots | At prediction batch time or after first pitch |

We couldn’t gate PLAY promotion without real closing snapshots.

### Closing odds capture (MARKET-009, Sep 11)

Built the missing infrastructure:

- `scripts/closing_odds_capture.py` — fetch odds 15–60 min before first pitch
- `state/predictions/odds_closes.jsonl` — append-only close records
- CLV study updated to prefer `odds_closes` over batch-time `odds_books`

Retrospective backfill (earliest prediction anchor):

| Metric | Value |
|--------|-------|
| Strict CLV N | **113** |
| Mean CLV | +0.10 pp (tiny) |
| ROI on strict cohort | **−10.2%** |
| Verdict | **Case 2:** positive CLV, negative ROI |

ADR-007 (PLAY staking policy) remains **PROPOSED / blocked**.

### Operator deployment (OPS-004, Sep 11–13)

Installed on the homelab:

- One-shot **backfill service** (109→144 rows over time)
- **10-minute timer** for live pregame capture
- Runbook section in `docs/homelab-operations.md`

**Live capture is working.** Example: game `824955` (Sep 12 night slate) — close
snapshot at 6:09 PM PT, prediction at 6:06 PM, first pitch 6:40 PM,
`capture_method: live_pregame`.

Early prospective CLV (latest anchor, N=31): still Case 2, too small to conclude.

---

## Sep 13 check-in: Raw model vs edge (discussion, not a decision)

With **343 resolved games** on the journal we ran an informal comparison:

| Strategy | Win rate | Flat ROI |
|----------|----------|----------|
| **Raw model favorite** | **58.3%** | **+0.4%** |
| Edge-selected side | 45.2% | −6.4% |
| Current PLAY subset | 41.0% | worse |

On **163 crossover games** (model favorite ≠ edge side): raw favorite wins 64%,
edge side wins 36%.

**Draft direction (not adopted):** treat raw model picks as the primary shadow
policy; stop using edge *sign* to override the model’s side until validated.

---

## What we did *not* change

- **ADR-006 locked model** — no retrain, no feature changes
- **Production PLAY threshold** — still 2%, display-only legacy
- **ADR-007** — drafted but not accepted
- **Git remote** — `main` is several commits ahead of `origin`; not pushed in this period

---

## Where we stand today (Sep 13, 2026)

```text
MODEL          ✅  Monitor — discrimination matches holdout
MARKET/PLAY    ⚠️  Not production-ready — edge selection underperforms raw model
CLV            📊  Pipeline works; signal too weak / sample too small
OPS            ✅  Daily + enrich + closing-odds timers running on homelab
DATA           ✅  144 closing snapshots (109 backfill + 35 live)
```

**Honest summary:** We built the instrumentation to *ask* the right questions.
The answers so far: the model picks winners more often than the PLAY layer;
closing-line evidence doesn’t rescue edge-based betting; live capture is now
collecting the data we were missing.

---

## Likely next steps

1. **Accumulate live closes** (latest anchor, 100+ strict games) and rerun CLV
2. **Track raw-model shadow** vs baseline PLAY on the dashboard
3. **Push `main` to GitHub** when ready
4. **File MARKET-010** (or similar) if we formalize raw-model selection policy

---

## How to update this journal

Add a new dated section at the top of *Last three weeks* (or start a new
monthly block when this one gets long). Copy the template:

```markdown
### YYYY-MM-DD — Title

What shipped, what we measured, what we decided *not* to do, and one sentence
on what it means.
```

---

## Quick links

| Resource | Path |
|----------|------|
| PLAY policy study | `reports/market/play-policy-study-market-006.md` |
| ML-015 live diagnostic | `docs/research/ml-015-prospective-model-market-diagnostic.md` |
| CLV rerun (N=113) | `reports/market/clv-study-market-009-rerun.md` |
| Homelab runbook | `docs/homelab-operations.md` |
| ADR-007 (PROPOSED) | `docs/decisions/ADR-007-play-staking-policy.md` |
