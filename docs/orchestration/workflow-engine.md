# Alfred Workflow Engine

The workflow engine turns Alfred's orchestration templates from prose into **executable,
validated, testable** artifacts. A workflow is a JSON file describing stages (each an agent + a
task), their dependencies, and optional control flow. The engine
(`scripts/workflow.py`, stdlib-only) validates the graph, schedules it into parallel waves,
renders each stage's task from its dependencies' outputs, and runs it.

Launcher: `scripts/workflow-run.ps1`. Specs live in `workflows/`. Tests: `scripts/test_workflow.py`
(engine logic) and `scripts/test_backends.py` (executors, prompt assembly, cost). This document is
the reference for the spec format and execution model.

## Backends

The engine is backend-agnostic: it schedules the DAG and delegates each stage to an **executor**.
`--backend` selects one:

| Backend | Executor | Notes |
|---------|----------|-------|
| `dry` (default) | echo | Renders every stage, spawns nothing. |
| `claude` | `claude --agent <name> -p` | Claude Code CLI. |
| `api` | Anthropic Messages API | Needs `ANTHROPIC_API_KEY`; reports token cost. |
| `kiro` | `kiro-cli chat --no-interactive` | The original Kiro path. |
| `auto` | first available of the above | Never silently downgrades; `doctor` shows the pick. |

Executor construction, model resolution, and cost estimation live in `scripts/backends.py`
(stdlib-only, fully unit-tested offline via injected `opener`/`sleeper`).

## Commands

```
python scripts/workflow.py doctor                             # which backends are live
python scripts/workflow.py validate <spec> [--check-agents]   # structure + agent registry
python scripts/workflow.py plan     <spec>                     # parallel execution waves
python scripts/workflow.py graph    <spec>                     # Mermaid diagram
python scripts/workflow.py run      <spec> --task "..." [--execute] [--backend B]
                                                               [--budget N] [--parallel N]
                                                               [--var k=v] [--resume <dir>]
python scripts/workflow.py runs     [--limit N]                # recent run history + cost
```

`run` is a **dry run by default** (renders every stage, spawns nothing). `--execute` runs stages
via the selected backend and records artifacts under `memory/workflows/<run>/`.

## Spec format

```json
{
  "name": "feature",
  "description": "one line",
  "vars": { "branch": "main" },
  "budget": 20,
  "stages": [
    { "name": "plan",   "agent": "alfred-planner", "task": "Break down: {task}", "depends_on": [] },
    { "name": "code",   "agent": "alfred-coder",   "task": "Implement:\n{stage.plan}",
      "depends_on": ["plan"], "timeout": 1800 },
    { "name": "review", "agent": "alfred-reviewer","task": "Review.\n{deps}",
      "depends_on": ["code"],
      "loop_to": { "target": "code", "trigger": "NEEDS_CHANGES", "max_iterations": 3, "backoff": 5 } },
    { "name": "ship",   "agent": "alfred-devops",  "task": "Ship.\n{stage.review}",
      "depends_on": ["review"],
      "when": { "stage": "review", "contains": "APPROVED" } }
  ]
}
```

### Fields

| Field | Scope | Meaning |
|-------|-------|---------|
| `name`, `description` | spec | Identity. |
| `vars` | spec | Defaults for `{vars.KEY}` placeholders (overridable with `--var`). |
| `budget` | spec | Max total stage executions this run (also `--budget`). Loops count against it. |
| `name`, `agent`, `task` | stage | Required. `agent` must be a registered agent (with `--check-agents`). |
| `depends_on` | stage | Stages that must finish first. Defines the DAG. |
| `timeout` | stage | Seconds; passed to the executor. On overrun the stage returns a `[TIMEOUT]` marker. |
| `retries` | stage | Re-run count for a stage that returns `[ERROR]`/`[TIMEOUT]`. Attempts = `retries + 1`. |
| `retry_backoff` | stage | Base seconds between retry attempts (exponential + jitter). Default 2. |
| `on_error` | stage | `"continue"` (default) carries the error forward; `"fail"` aborts the whole run. |
| `when` | stage | `{ "stage": X, "contains"/"not_contains": "TEXT" }`. Skip the stage unless the condition holds. |
| `loop_to` | stage | `{ target, trigger, max_iterations, backoff? }`. Bounded runtime back-edge. |

### Task placeholders
`{task}` (overall objective) · `{deps}` (all dependency outputs) · `{stage.<name>}` (one stage's
output) · `{vars.<key>}`.

## Execution model

1. **Validate.** Unique names; every `depends_on` resolves; no self-deps; **no cycles** (checked
   over `depends_on` edges only - `loop_to` back-edges are excluded). `when`/`timeout`/`budget`/
   `backoff` are range-checked. With `--check-agents`, every `agent` must exist in `.kiro/agents/`.
2. **Schedule.** Kahn topological sort, grouped into **waves**: wave 0 = no-dependency stages;
   stages in the same wave are independent and **run concurrently** (thread pool, up to
   `--parallel`/`spec.parallel`, default 4). `--parallel 1` forces the sequential path, bit-for-bit
   identical to single-threaded execution - use it when stages write to the same files.
3. **Render + run.** Each stage's task is rendered with its dependencies' outputs (from *earlier*
   waves only, so concurrent siblings never observe a half-written output) and run via the executor.
4. **Retries.** A stage returning `[ERROR]`/`[TIMEOUT]` is re-run up to `retries` times with
   exponential backoff. If it still fails and `on_error: "fail"`, the run aborts; otherwise the
   error is carried forward as that stage's output.
5. **Conditional skip.** If a stage's `when` fails, it is skipped (output empty) and recorded.
6. **Loops.** If a stage's output contains its `loop_to.trigger` and iterations remain, the engine
   jumps back to `target` and re-runs forward. Between retries it waits
   `backoff * 2^(n-1) + jitter` seconds (exponential backoff with full jitter). A hard safety cap
   prevents runaway loops even if a trigger never clears.
7. **Budget.** Before each execution, if the run has hit `budget` stage-executions, it stops.
8. **History.** On `--execute`, `memory/workflows/<name>-<stamp>/` gets per-stage `*.md` outputs
   and a `run.json` (stages executed, skipped, loop counts, per-stage status/duration, and
   `backend`/`model`/`cost_usd` plus a run `cost_usd` total). `runs` lists these newest-first with
   spend - the engine's observability surface.
9. **Resume.** `--resume <run-dir>` reloads the outputs of stages that finished `ok` and re-runs
   only the rest - crash/failure recovery without repeating expensive successful stages.

## Relationship to production workflow engines

This is deliberately a **single-machine agent** orchestrator, not a distributed service. But it
implements the core primitives of engines like Argo Workflows / Airflow / Temporal for the agent
domain: declarative specs, a DAG scheduler with parallelism and fan-in, bounded retries with
backoff, conditional execution, resource budgets, timeouts, and run history. See
`docs/orchestration/kubernetes-decision.md` for why we adopt these *patterns* rather than a
container platform. Honest non-goals (not needed at n=1): a persistent scheduler daemon, cron
triggers, distributed workers across machines, and a web UI. Crash-resume (`--resume`) and
in-process wave parallelism (`--parallel`) *are* implemented.

## Extending

- New pipeline: `powershell -File scripts/new-workflow.ps1 -Name <name>` scaffolds + validates a
  spec skeleton. Fill in the tasks; add `depends_on`, `when`, `loop_to`, `timeout`, `retries`,
  `on_error` as needed.
- New feature: keep the graph/executor logic pure (so `scripts/test_workflow.py` and
  `scripts/test_backends.py` can cover it without spawning agents), then add a test alongside the
  existing ones.
