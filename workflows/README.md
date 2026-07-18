# Alfred Workflows

Declarative, version-controlled multi-agent pipelines. Each file here is a **DAG spec**:
stages (an agent + a task), their dependencies, and optional loops. The engine
(`scripts/workflow.py`) validates the graph, computes parallel execution waves, renders each
stage's task from its dependencies' outputs, and runs it via `kiro-cli chat`.

This turns Alfred's orchestration **templates** (previously prose in
`.kiro/skills/orchestration`) into **executable, testable artifacts**.

## Use

```powershell
# Preview the plan (no agents run)
powershell -File scripts\workflow-run.ps1 -Workflow feature -Plan

# Mermaid diagram of the DAG
powershell -File scripts\workflow-run.ps1 -Workflow feature -Graph

# Dry run (renders every stage, spawns nothing)
powershell -File scripts\workflow-run.ps1 -Workflow feature -Task "Add pagination to /users"

# For real (spawns agents via kiro-cli)
powershell -File scripts\workflow-run.ps1 -Workflow bugfix -Task "Login 500s on empty body" -Execute
```

Or call the engine directly:

```
python scripts/workflow.py validate workflows/feature.json --check-agents
python scripts/workflow.py plan     workflows/feature.json
python scripts/workflow.py graph    workflows/feature.json
python scripts/workflow.py run      workflows/feature.json --task "..."   # dry by default; add --execute
```

## Spec format

```json
{
  "name": "feature",
  "description": "one line",
  "vars": { "branch": "main" },
  "stages": [
    { "name": "plan", "agent": "alfred-planner", "task": "Break down: {task}", "depends_on": [] },
    { "name": "code", "agent": "alfred-coder",   "task": "Implement:\n{stage.plan}", "depends_on": ["plan"] },
    { "name": "review", "agent": "alfred-reviewer", "task": "Review.\n{deps}", "depends_on": ["code"],
      "loop_to": { "target": "code", "trigger": "NEEDS_CHANGES", "max_iterations": 3 } }
  ]
}
```

**Task placeholders:** `{task}` (overall objective), `{deps}` (all dependency outputs),
`{stage.<name>}` (one stage's output), `{vars.<key>}`.

**Rules enforced by the validator:** unique stage names; every `depends_on` resolves; no
self-dependency; **no cycles** (over `depends_on` edges); `loop_to.target` exists with a
non-empty `trigger` and `max_iterations >= 1`; with `--check-agents`, every `agent` must be a
registered agent in `.kiro/agents/`.

## Shipped templates

| File | Shape | Pipeline |
|------|-------|----------|
| `feature.json`  | plan -> fan-out -> fan-in -> gate | planner -> (coder ∥ researcher) -> tester -> reviewer(loop) -> devops CI |
| `bugfix.json`   | iterate-until-green loop | debugger -> coder -> tester(loop) -> reviewer |
| `research.json` | fan-out / fan-in | planner -> 3× researcher (parallel) -> synthesize |
| `audit.json`    | parallel audit, fan-in | (security ∥ reviewer) -> architect report |
| `refactor.json` | characterize -> change -> verify | tester(baseline) -> coder -> tester(loop) -> reviewer(diff) |

## Loops

`loop_to` creates a bounded runtime back-edge: when the trigger text appears in a stage's
output, the engine jumps back to `target` and re-runs forward, up to `max_iterations` times.
This is the "iterate-until-green" and "review->revise" pattern, made explicit and bounded so
it never blind-retries forever.

Tests: `python scripts/test_workflow.py` (22 cases; graph, loops, rendering, validation).
