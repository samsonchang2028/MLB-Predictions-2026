# ADR-001: V1 Storage — DuckDB + Parquet

## Status

Accepted.

## Context

MLB game-level data for a few seasons does not require distributed data infrastructure. The primary challenge is reproducibility and temporal correctness, not scale.

## Decision

Use:

- Parquet for durable analytical datasets,
- DuckDB for local relational querying and transformation,
- raw JSON/API payloads for immutable source retention.

Organize data as Bronze / Silver / Gold.

## Consequences

Positive:

- simple local development,
- fast analytical queries,
- portable files,
- low infrastructure overhead.

Negative:

- not designed for large multi-user production workloads,
- future cloud deployment may require storage adaptation.

Do not introduce Spark, Kafka, or a remote warehouse in V1 without a new ADR.
