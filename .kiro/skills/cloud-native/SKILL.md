---
name: cloud-native
description: Containers, Kubernetes, IaC, and cloud services with a simplest-infra-first, cost-aware bias. Use when provisioning or evaluating infrastructure, containerizing an app, or deciding whether/when to adopt an orchestrator.
---

# Cloud-Native

## Prime directive: simplest infra that meets the need
Complexity is a cost you pay forever (patching, securing, upgrading, paying, on-call). Add a
layer only when the requirement demands it, and justify it. Reach for the platform your scale
actually needs — not the one that looks impressive.

## The infrastructure ladder (climb in order; stop when the need is met)
1. **Serverless / managed** — Cloudflare Workers, Cloud Run, Lambda, HF Spaces. Scales to zero,
   no servers to run. Best default for a single app or an API with bursty traffic.
2. **One VM + `docker compose`** — a handful of cooperating services, one machine, simple ops.
3. **Managed container service** — ECS Fargate / Cloud Run / Azure Container Apps. Containers
   without a cluster to operate.
4. **Managed Kubernetes** (GKE/EKS/AKS) — only with *many* services, *multiple* nodes, and a team
   to operate it. Self-managed k8s is the last resort.

> Do not skip rungs. Most projects never need rung 4. See `docs/orchestration/kubernetes-decision.md`.

## Containers
- Multi-stage builds; tiny final images (distroless/alpine where sane). Pin base image digests.
- One concern per container. Config via env/secrets, never baked in. Non-root user.
- Healthchecks defined. `.dockerignore` to keep context small and secrets out.

## Kubernetes (when rung 4 is truly justified)
- Declarative manifests in git. Set resource requests/limits and liveness/readiness probes.
- Secrets in a secret manager (External Secrets / sealed), never plain manifests.
- Prefer managed control planes and managed add-ons. Helm/Kustomize for reuse.
- Namespaces + RBAC least-privilege; network policies default-deny.

## Infrastructure as Code
- Everything reproducible: Terraform / Bicep / CloudFormation / Pulumi. No click-ops.
- **Plan before apply.** Show the diff (`terraform plan`), the cost impact, and rollback steps.
- **Never apply/destroy live infra without Owner approval** (safety gate). Read state first.
- Modules/stacks for reuse. Tag every resource for cost attribution.

## Cost awareness
- Right-size from day one; flag idle/over-provisioned resources. Use spot/preemptible for
  fault-tolerant work and reserved/committed for steady baselines. Set budgets + alerts.

## Reliability handoff
- Observability, SLOs, and incident response belong to `alfred-sre`.
- Architectural calls (multi-region, data replication, service mesh) go to `alfred-architect`.
- IAM/network-exposure review goes to `alfred-security`.

## Anti-patterns
- A Kubernetes cluster for one app on one machine. Serverless would do.
- Unpinned images/deps; secrets in manifests or env files committed to git.
- In-place production changes with no plan, no diff, and no rollback.
