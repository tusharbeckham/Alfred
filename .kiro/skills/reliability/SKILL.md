---
name: reliability
description: Site reliability engineering - observability (metrics/logs/traces), SLOs and error budgets, incident response, runbooks, and reliability patterns. Use when making a system observable, defining reliability targets, or responding to an incident.
---

# Reliability (SRE)

## Mindset
Reliability is a feature, measured in the user's experience, not the server's uptime. Diagnose
before acting; prefer reversible changes; automate toil away. Read state and evidence first.

## Observability (you can't fix what you can't see)
- **Metrics** — instrument with **RED** (Rate, Errors, Duration) for services and **USE**
  (Utilization, Saturation, Errors) for resources. Emit structured, low-cardinality metrics.
- **Logs** — structured (JSON), correlated by request/trace id, leveled. No secrets in logs.
- **Traces** — distributed tracing across service boundaries; sample smartly.
- Every service exposes a health/readiness endpoint and emits the golden signals.

## SLOs and error budgets
- Pick **SLIs** that track user-facing behavior (success rate, latency percentiles p95/p99).
- Set an **SLO** (target) and derive an **error budget** (1 - SLO). Spend it deliberately.
- **Alert on burn rate**, not raw thresholds — fast-burn (page) vs slow-burn (ticket). Cut noise.

## Incident response
1. **Detect** → declare severity. 2. **Triage** → mitigate first (roll back, drain, failover),
diagnose second. 3. **Communicate** → single source of truth, regular updates. 4. **Resolve**.
5. **Blameless post-mortem** → timeline, root cause, and concrete, owned action items.
- Restarts/scale/deploy in production are **gated** — recommend and get approval; never silent.

## Runbooks
- Every alert has a runbook: symptoms, quick triage, remediation steps, escalation, and rollback.
- Runbooks are living docs — update after every incident. Prefer a link in the alert itself.

## Reliability patterns
- Timeouts on every network call; **retries with backoff + jitter** (bounded, idempotent only).
- **Circuit breakers** and **bulkheads** to contain failure. Graceful degradation over hard fail.
- Backpressure and queue-depth limits. Idempotency keys for at-least-once delivery.
- Chaos/failure drills to validate assumptions before real incidents do.

## Capacity
- Load-test to find the knee. Model growth. Define autoscaling triggers **before** saturation.
- Track saturation signals (queue depth, CPU steal, GC pressure) as leading indicators.

## Handoffs
- Provisioning/infra → `alfred-cloud`. Architecture (multi-region, replication) → `alfred-architect`.
- Root-cause of a specific bug → `alfred-debugger`. Security exposure → `alfred-security`.

## Anti-patterns
- Alerting on causes instead of symptoms (noisy, ignored). Dashboards no one reads.
- Retries without backoff (retry storms). Post-mortems that assign blame instead of fixes.
