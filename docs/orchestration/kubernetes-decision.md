# Decision: Should Alfred use Kubernetes?

**Status:** Decided — **No (not now).** Adopt Kubernetes *patterns*, not the *platform*.
**Date:** 2026-07-18
**Owner question:** "Shall we use Kubernetes? Are we better than Kubernetes, or reaching its
level in workflow?"

---

## TL;DR (the direct answer)

1. **Should we run Kubernetes? No.** Not for Alfred as it exists today. It would add a large,
   ongoing operational burden for zero benefit at our scale (one machine, one user, ephemeral
   CLI tasks). Running k8s to orchestrate Alfred would violate Alfred's own new
   *simplest-infra-first* principle (see `alfred-cloud`).
2. **Are we "better than Kubernetes"? Wrong axis.** Kubernetes and Alfred solve **different
   problems at different layers**. Kubernetes orchestrates **containers/services**; Alfred
   orchestrates **AI agents**. Comparing them is apples-to-oranges — neither is "better," they
   sit in different domains.
3. **Are we "reaching its level in workflow"? In our domain, yes — conceptually.** For
   *workflow orchestration as a discipline* (declarative specs, a DAG scheduler, parallelism,
   fan-in, bounded retries, validation gates), Alfred's new workflow engine now implements the
   same core primitives that engines like **Argo Workflows / Airflow / Temporal** use — but for
   the **agent** domain rather than the container domain. That is the right bar to measure
   against, and we now clear its core concepts.

---

## Why comparing Alfred to Kubernetes is a category error

| | Kubernetes | Alfred |
|---|---|---|
| **Orchestrates** | Containers / long-running services | AI agents / cognitive tasks |
| **Unit of work** | A Pod (a running process) | A stage (an agent + a task) |
| **Goal** | Availability, scaling, self-healing, rollout | Solve a problem correctly via the right specialists |
| **Lifetime** | Long-running, always-on | Ephemeral, per-invocation |
| **Scale axis** | Nodes, replicas, traffic | Agents, credits, task complexity |
| **Bottleneck** | CPU/mem/network across a fleet | Model credits and reasoning quality |

Kubernetes is a **container orchestrator**. If Alfred ever *packaged services in containers and
needed to run many of them across machines*, k8s would be a candidate to run **underneath**
Alfred. It is not a substitute for what Alfred's workflow engine does, and Alfred's workflow
engine is not trying to replace k8s. They are one layer apart.

## Where Alfred's workflow layer genuinely rivals workflow engines

The closer comparison is not core k8s but **workflow engines** (Argo Workflows, Airflow,
Temporal, Prefect). With the new engine (`scripts/workflow.py` + `workflows/*.json`), Alfred now
has real analogues of their primitives:

| Concept | Argo/Airflow/Temporal | Alfred (today) |
|---|---|---|
| Declarative pipeline | YAML DAG / DSL | `workflows/*.json` specs |
| Scheduler | DAG scheduler, topological | `topo_order()` + `waves()` (Kahn's) |
| Parallelism | Parallel tasks / branches | Same-wave stages run in parallel |
| Fan-in | Join / dependency merge | `depends_on` + `{deps}` rendering |
| Bounded retry / loop | `retryStrategy`, backoffLimit | `loop_to` with `max_iterations` |
| Admission / validation | Schema + admission control | `validate --check-agents` gate |
| Health / readiness gate | Probes | CI gate stage (`CI: PASS/FAIL`) |
| Run artifacts | Logs per task | `memory/workflows/<run>/*` |

**Honest gaps** (what mature engines still have that we don't): persistent run state and resume
after crash, a scheduler daemon / cron triggers, distributed execution across workers, a UI,
and metrics/backoff-with-jitter. We don't need most of these at n=1, but they mark the honest
distance to a production workflow engine.

**Verdict on "reaching its level":** for **agent** workflow orchestration on a single operator's
machine, Alfred's engine now embodies the core scheduling/looping/validation concepts of the big
workflow engines. It is not a distributed, persistent, always-on engine — and doesn't need to be.

## Why *not* Kubernetes now (the concrete reasons)

- **One machine, one user.** k8s earns its complexity across *many nodes and services*. Alfred
  runs on the Owner's Windows PC. A cluster of one is pure overhead.
- **Ephemeral, not always-on.** Alfred's work is CLI-invoked bursts, not 24/7 services that need
  self-healing and rolling deploys.
- **The bottleneck is credits, not compute.** Scaling pods doesn't buy anything when the limiting
  resource is model credits.
- **The only hosted surface is already serverless — correctly.** The `deploy/` web app runs on a
  **Cloudflare Worker** (+ an optional HF Space). Serverless scales to zero, costs little, and
  needs no cluster to operate. This is the *right* choice and the opposite of needing k8s. The
  2025 industry trend is exactly this: reach for serverless/managed first, k8s only when the
  scale/complexity demands it. [1][2][4]
- **Operational cost is real.** A cluster must be patched, secured, upgraded, monitored, and paid
  for. That is a second job, for no current benefit.

## When Kubernetes *would* earn its place

Revisit this decision only if **all** of these become true:
- Alfred (or its web app) becomes an **always-on, multi-tenant service** with real, sustained
  traffic; and
- it comprises **several cooperating services** (APIs, queues, workers, self-hosted models); and
- it must run across **multiple nodes** for availability or throughput; and
- there is capacity to **operate** a cluster.

Even then, climb the ladder in order — don't skip to k8s:

1. **One managed VM** + `docker compose`.
2. **Managed container service** — Cloud Run / ECS Fargate / Azure Container Apps (containers
   without a cluster to run).
3. **Managed Kubernetes** (GKE/EKS/AKS) — only when you truly have many services, many nodes, and
   a team. Self-managed k8s is the last resort. [1][3][5]

## Recommendation

- **Do not** introduce Kubernetes into Alfred now.
- **Do** keep the hosted web app on serverless/managed (current Cloudflare setup).
- **Do** borrow k8s/workflow-engine *patterns* we don't yet have and that are cheap to add:
  - **Backoff on loops** (small delay/jitter between `loop_to` retries).
  - **Per-run resource budgets** (a credit/step cap per workflow run — the analogue of resource
    limits), surfaced by `alfred-status`.
  - **Readiness/health gates** between waves (extend the CI-gate pattern).
  - **Run observability** via the new `alfred-sre` agent (SLO-style success/latency tracking of
    workflow runs).
- **Ownership:** `alfred-cloud` owns this decision and any future infra; `alfred-architect`
  signs off if the "when it would earn its place" conditions are ever met.

## Sources

1. Kubernetes vs Simpler Alternatives: 2025 Battle Guide — mashblog.com
2. Kubernetes vs. Serverless: Strategic Insights for Choosing Wisely — dev.to
3. Why Teams Are Moving Beyond Kubernetes: Simpler Orchestration Alternatives (2025) — medium.com
4. Is Kubernetes Becoming Legacy Tech? What Modern Teams Use Instead (2025) — aws.plainenglish.io
5. Kubernetes vs. Serverless: When to Choose Which? — red-gate.com / Simple Talk

_Retrieved 2026-07-18 via `scripts/alfred-web.ps1` (keyless). Titles corroborate the
serverless/managed-first consensus; the architectural judgment above is Alfred's own._
