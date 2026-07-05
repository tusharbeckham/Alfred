---
name: architecture
description: System design and architecture decisions — structure, boundaries, tradeoffs, and technology choices. Use when designing a system, evaluating options, or making a decision with long-term impact.
---

# Architecture

## Approach
1. CLARIFY the requirements: functional, non-functional (scale, latency, reliability,
   security, cost), and constraints. State assumptions explicitly.
2. IDENTIFY the core domain and the boundaries (modules, services, data ownership).
3. PROPOSE 2–3 options with honest tradeoffs, not a single foregone conclusion.
4. RECOMMEND one, with the reasoning and the conditions under which you'd choose otherwise.
5. RECORD the decision as an ADR in memory/decisions.md (context, decision, consequences).

## Design principles
- Favor simplicity; add complexity only when a requirement demands it (YAGNI).
- High cohesion, low coupling. Clear interfaces; hide implementation details.
- Design for change: isolate volatile parts behind stable boundaries.
- Make illegal states unrepresentable. Push validation to the edges.
- Prefer boring, proven technology unless there's a compelling reason.

## Tradeoff axes to weigh
Consistency vs availability · sync vs async · monolith vs services · build vs buy ·
normalized vs denormalized · latency vs throughput vs cost.

## Reviewing an existing system
- Map the actual data flow and dependencies before proposing changes.
- Find the load-bearing assumptions and the riskiest coupling.
- Recommend incremental, reversible refactors over big rewrites.

## Output
A crisp design: context, options+tradeoffs, recommendation, diagram (mermaid if helpful),
and an ADR entry. Flag security and scaling risks early.
