# V1 Task Graph

## Goal

Build a reproducible MLB moneyline forecasting pipeline that:

- ingests historical/current baseball data,
- ingests timestamped moneyline odds,
- creates point-in-time-safe features,
- compares multiple model families,
- evaluates expanding and rolling training windows,
- preserves 2026 as the final holdout,
- produces daily predictions,
- exposes results in a lightweight Streamlit app.

## Graph

```text
META-001 Repository/agent foundation
          │
          ▼
DATA-001 Storage foundation
          │
     ┌────┴────┐
     ▼         ▼
DATA-002     DATA-003
MLB ingest   Odds ingest
     │         │
     └────┬────┘
          ▼
DATA-004 Normalized datasets
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
FEAT-001 FEAT-002 FEAT-003
Team     Starter  Bullpen
   │      │      │
   └──────┼──────┘
          ▼
FEAT-004 Feature matrix
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
ML-001  ML-002  ML-003
LogReg    RF     XGB
   │      │      │
   └──────┼──────┘
          ▼
ML-004 Walk-forward validation
          │
   ┌──────┴──────┐
   ▼             ▼
ML-005         ML-006
Expanding      Rolling windows
   │             │
   └──────┬──────┘
          ▼
ML-007 Model comparison
          │
          ▼
ML-008 Probability calibration
          │
          ▼
MARKET-001 Market/no-vig/edge engine
          │
          ▼
PIPE-001 Daily prediction pipeline
          │
    ┌─────┴─────┐
    ▼           ▼
APP-001       OBS-001
Streamlit     Prediction journal
    │           │
    └─────┬─────┘
          ▼
APP-002 Performance dashboard
```

## Parallel execution groups

### After DATA-001

May run in parallel:

- DATA-002
- DATA-003

### After DATA-004

May run in parallel:

- FEAT-001
- FEAT-002
- FEAT-003

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
