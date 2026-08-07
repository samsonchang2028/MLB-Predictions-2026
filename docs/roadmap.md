# V1 Task Graph

## Goal

Build a reproducible MLB moneyline forecasting pipeline that:

- ingests historical/current baseball data,
- certifies the 2021-2025 historical MLB dataset before dependent feature/model work,
- ingests live timestamped moneyline odds,
- ingests the finalized historical odds archive for opening-market benchmarking,
- creates point-in-time-safe features,
- compares multiple model families,
- evaluates expanding and rolling training windows,
- preserves 2026 as the final holdout,
- produces daily predictions,
- exposes results in a lightweight Streamlit app.

## Graph

```text
META-001 Repository/agent foundation
  |
  v
DATA-001 Storage foundation
  |
  +--> DATA-002 MLB schedule ingestion
  |      |
  |      v
  |    DATA-004 Normalized schedule/live-odds contracts
  |      |
  |      v
  |    DATA-005 MLB game-detail/pitcher backfill
  |      |
  |      v
  |    DATA-006 Historical MLB data validation
  |      |
  |      v
  |    DATA-007 Historical MLB data certification
  |      |
  |      v
  |    FEAT-002 Starter features
  |    FEAT-003 Bullpen features
  |
  +--> DATA-003 Live timestamped odds ingestion
  |
  +--> DATA-008 Historical odds archive ingestion
         |
         v
       DATA-009 Historical odds archive validation/mapping audit

FEAT-001 Team features is complete from DATA-004, but downstream use on the
real 2021-2025 dataset is gated by DATA-007 certification.

DATA-007 + FEAT-001 + FEAT-002 + FEAT-003
  |
  v
FEAT-004 Feature matrix
  |
  +--> ML-001 Logistic regression
  +--> ML-002 Random forest
  +--> ML-003 XGBoost
          |
          v
        ML-004 Walk-forward validation
          |
          +--> ML-005 Expanding-window experiment
          +--> ML-006 Rolling-window experiments
                  |
                  v
                ML-007 Model/window comparison
                  |
                  v
                ML-008 Probability calibration
                  |
                  v
                MARKET-001 Market/no-vig/edge engine
                  ^
                  |
                DATA-009 feeds historical opening-market benchmark inputs

MARKET-001
  |
  v
PIPE-001 Daily prediction pipeline
  |
  +--> APP-001 Streamlit daily board
  +--> OBS-001 Prediction journal
          |
          v
        APP-002 Performance dashboard
```

## Parallel execution groups

### Current ready batch

May run in parallel:

- DATA-005
- DATA-008

These own mostly separate MLB and odds ingestion surfaces. DATA-009 should wait
for DATA-008 and can use existing DATA-004 game candidates.

### After DATA-001

May run in parallel:

- DATA-002
- DATA-003
- DATA-008

### After DATA-007

May run in parallel:

- FEAT-002
- FEAT-003

FEAT-001 is already complete, but model-critical downstream use should be
revalidated against the certified real historical dataset.

### After FEAT-004

May run in parallel:

- ML-001
- ML-002
- ML-003

## Integration principle

Parallel tasks should own separate modules.

Example:

```text
src/features/team.py
src/features/starter.py
src/features/bullpen.py
```

A later integration task should own the aggregation point:

```text
src/features/build.py
```

Avoid having parallel workers heavily edit the same file.

## Historical data gates

Validation and certification are separate:

- DATA-006 executes individual Bronze/Silver/data/temporal/leakage checks.
- DATA-007 decides whether a concrete 2021-2025 dataset build is certified
  `PASS` or `FAIL`.

Feature/model tasks that depend on the 2021-2025 historical dataset must not be
marked ready when DATA-007 is failed or missing.

Historical opening-odds benchmarking and live timestamped odds are intentionally
separate methodologies. Historical archive results support "model edge versus
opening market" and "simulated ROI at opening prices"; live predictions require
an exact odds snapshot timestamp before first pitch.

## V1 non-goals

Do not add unless a new accepted task/ADR explicitly changes scope:

- neural networks,
- automated wagering,
- Kafka,
- Spark,
- Kubernetes,
- microservices,
- complex cloud infrastructure,
- generic feature-store platform,
- confirmed-lineup modeling,
- batter-vs-pitcher modeling,
- weather modeling,
- Kelly sizing,
- multi-sports support.
