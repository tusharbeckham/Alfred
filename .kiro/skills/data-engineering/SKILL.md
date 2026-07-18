---
name: data-engineering
description: Data engineering - ETL/ELT pipelines, data modeling, warehousing, and data-quality checks that are idempotent, tested, and cost-aware. Use when building or reviewing data pipelines and models.
---

# Data Engineering

## Prime directives
Pipelines are **idempotent**, **tested**, and **cost-aware**. Re-running a pipeline must never
duplicate or corrupt data. Bad data must fail loudly, not flow silently downstream. Compute and
storage cost is a design input, not an afterthought.

## Idempotency (non-negotiable)
- Prefer **upsert/merge** (on a stable key) over blind insert. Use deterministic keys.
- **Partition + checkpoint** so a re-run reprocesses only what it must and can restart cleanly.
- Make transforms pure functions of their input window; no hidden dependence on wall-clock or
  prior run state beyond explicit checkpoints.

## Data quality (build it in, every pipeline)
- Schema validation at the boundary; reject or quarantine non-conforming rows.
- Assertions: not-null, uniqueness, referential integrity, row-count/volume bounds, freshness.
- Fail the run on violations (or route to a dead-letter store) - never pass bad data on.
- Tools: Great Expectations, dbt tests, or custom assertions in the pipeline.

## Modeling
- Choose the model for the read pattern: **dimensional (Kimball)** for analytics/BI, normalized
  for OLTP, wide tables for ML features, data vault for auditable integration.
- Separate raw (immutable landing) → cleaned/conformed → marts. Don't transform in place.
- Slowly-changing dimensions: pick the SCD type deliberately and document it.

## Data contracts + schema evolution
- Schemas are **versioned** and backward-compatible by default (add nullable columns; don't
  repurpose or drop). Breaking changes need a migration plan + downstream-impact assessment.
- Publish the contract (columns, types, semantics, freshness SLA) so consumers can rely on it.

## Cost + performance
- **Incremental over full refresh.** Partition pruning and predicate pushdown. Columnar formats
  (Parquet/ORC) and compression. Monitor scan volume and storage growth; flag runaway cost early.
- Right-size compute; avoid tiny-file explosions; compact when needed.

## Formats + platforms
- Files: Parquet, Avro, Delta Lake, Iceberg (ACID + time travel on the lake).
- Batch/stream: dbt, Spark, Airflow/Dagster; warehouses: BigQuery, Snowflake, Redshift, DuckDB.

## Handoffs
- Warehouse architecture/partitioning strategy → `alfred-architect`.
- Cluster sizing, IAM for data services, provisioning → `alfred-cloud`.
- Query/pipeline performance tuning → `alfred-perf`.

## Anti-patterns + safety
- Non-idempotent pipelines; silent bad data; unversioned breaking schema changes; full refreshes
  that scan everything nightly. **Never** truncate/drop/mutate a production store or run a
  destructive migration without explicit Owner approval - read and propose, don't execute.
