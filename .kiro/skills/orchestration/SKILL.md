---
name: orchestration
description: Designing and running multi-agent DAG pipelines — staffing, parallelism, fan-in, and loop patterns. Use when coordinating multiple agents or choosing a workflow.
---

# Orchestration

## Core idea
Decompose an objective into stages, wire dependencies as a DAG, run independent stages in
parallel, and fan results into an integrator. Use the `subagent` tool.

## Workflow templates
- **Feature**: planner → (coder ∥ researcher) → tester → reviewer → devops(CI gate).
- **Bugfix (loop)**: debugger → coder → tester → iterate-until-green → reviewer.
- **Research (fan-out)**: N researchers in parallel → synthesis stage.
- **Refactor**: reviewer(baseline+characterization tests) → coder → tester → reviewer(diff).
- **Multi-repo**: one coder per repo in its own worktree, all parallel → fan-in reviewer.
- **Audit**: security ∥ reviewer → consolidated report.

## Designing a DAG
1. List stages. For each: role (agent), crisp task, inputs, definition of done.
2. Mark true dependencies with `depends_on`. Everything else runs in parallel.
3. No cycles. Fan-in at a single integrator stage that verifies evidence.
4. Ask `alfred-prompt-engineer` for a tuned prompt when a stage is subtle.

## Loop patterns (own them; do not blind-retry)
- **Retry-on-fail**: on failure, DIAGNOSE → adjust the task/worker → re-run. Bounded.
- **Iterate-until-green**: run tests/evals; while failing and attempts remain, route to
  debugger→coder→tester. Escalate with a diagnosis at the bound.
- **Fan-out/fan-in**: parallelize independent work, integrate once.

## Staffing tips
- Match the specialist to the stage. Keep each subagent's scope narrow.
- Read-only stages (review, research) can be trusted agents (auto-approved).
- Record which pipeline shapes worked in the leader's memory for reuse.

## Anti-thrash
Two failures of the same approach → change the approach. If that deviates from the
objective, escalate to the manager rather than improvising.


## Executable engine (now real, not just prose)
The templates above ship as **runnable DAG specs** in `workflows/*.json`, executed by
`scripts/workflow.py` and launched via `scripts/workflow-run.ps1`:
- Preview the plan: `python scripts/workflow.py plan workflows/feature.json`
- Mermaid diagram:  `python scripts/workflow.py graph workflows/feature.json`
- Dry run (spawns nothing): `powershell -File scripts/workflow-run.ps1 -Workflow feature -Task "..."`
- Execute for real: add `-Execute`.

The engine enforces the DAG rules (unique names, resolvable deps, **no cycles**, bounded
`loop_to`) and computes parallel **waves** automatically. New workflows are just JSON, validated
against the live agent registry with `--check-agents`. Tests: `python scripts/test_workflow.py`.
This is the executable counterpart to the templates above — design the DAG here, run it there.
