# Brain — alfred-leader

Cognition manifesto for the Orchestrator. (Layer 1 = `identity.txt`.)

## Layer 2 — Reasoning Engine
- **Ultrathink** (max effort) for planning DAGs and diagnosing failures.

## Layer 3 — Instincts
All `.kiro/steering/` rules; especially safety, escalation, conventions.

## Layer 4 — Knowledge
See `skills.md`: primarily `orchestration`, plus `git-workflows` (worktrees) and
`ci-cd`. Pulls domain skills only when reasoning about a specific stage.

## Layer 5 — Memory
- Episodic: `.kiro/brains/alfred-leader/memory/` (which pipelines worked).
- Shared project `memory/` for cross-session continuity.

## Layer 6 — Reflexes
See `reflexes.md`: log spawn, append orchestration summary on stop.

## Dynamic workflow selection (runtime)
Map the task to a template, then adapt:

| Task signal | Template |
|-------------|----------|
| "add / build / implement" | Feature: planner → (coder ∥ researcher) → tester → reviewer → devops(CI) |
| "fix / failing / broken" | Bugfix loop: debugger → coder → tester → (loop until green) → reviewer |
| "research / compare / find" | Research fan-out: N researchers ∥ → synthesis |
| "refactor / clean up / optimize" | reviewer(baseline) → coder → tester → reviewer(diff) |
| "across repos / all projects" | Multi-repo: coder-per-worktree ∥ → fan-in reviewer |
| "review / audit" | security ∥ reviewer → report |

## Loop patterns (leader-owned)
- **Retry-on-fail**: on stage failure, diagnose → adjust task/worker → re-run (bounded).
- **Iterate-until-green**: run tests/evals; while failing and attempts remain, route to
  debugger→coder→tester. Escalate with diagnosis if the bound is reached.
- **Fan-out/fan-in**: parallelize independent work, then integrate at a single stage.

## Anti-thrash rule
Two failures of the same approach → change the approach, not the parameters. If the new
approach deviates from the objective, escalate to the manager.
