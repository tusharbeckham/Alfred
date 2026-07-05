---
inclusion: always
---

# Alfred — Escalation

Defines when an agent proceeds autonomously, when it escalates to the manager/leader, and
when it interrupts the Owner.

## Proceed autonomously (no interruption)
- Reversible, project-scoped work: reading, editing project files, writing/running tests,
  research, planning, drafting, refactoring within the project.
- Recoverable mistakes (a failing test, a lint error) — fix and continue.

## Escalate to alfred-leader / alfred-manager (not the Owner)
- A task needs multiple agents or a workflow decision → leader orchestrates.
- An agent is stuck after 2 genuine attempts → hand back to leader with a diagnosis, not
  another blind retry.
- Cross-cutting or architectural choices → loop in `alfred-architect`.

## Interrupt the Owner (only these)
- A safety gate is hit (see `safety.md`) and there is no safe alternative.
- Ambiguity that materially changes the outcome and cannot be resolved from context,
  memory, or reasonable default.
- A genuinely irreversible or high-blast-radius decision.
- The Owner explicitly asked to be consulted on this class of action.

## How to escalate
- Be specific: state the situation, the options, the recommendation, and what you need.
- During unsupervised runs, do **not** block waiting on the Owner — record the item on the
  Approvals List in `memory/todo.md` and continue with other safe work.

## Anti-loop rule
- If an approach fails twice, stop and diagnose the root cause. Try a fundamentally
  different approach; if that would deviate from the Owner's intent, escalate instead.
