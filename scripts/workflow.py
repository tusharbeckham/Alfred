#!/usr/bin/env python3
"""Alfred workflow engine - a declarative, testable multi-agent DAG runner.

Alfred's orchestration used to live only as prose templates in
`.kiro/skills/orchestration` and `prompts/orchestration`. This turns those
templates into *executable* artifacts: a workflow is a JSON file describing
stages (each an agent + a task), their dependencies, and optional loops. The
engine validates the graph (unique names, resolvable deps, no cycles), computes
parallel execution "waves" (topological levels), renders each stage's task with
the outputs of its dependencies, and runs it - by default by shelling out to
`kiro-cli chat --no-interactive --agent <agent>`.

Design goals:
  * Standard library only - runs anywhere Python 3.9+ is present.
  * Pure functions for the graph logic (load/validate/topo/waves) so they are
    unit-testable without spawning any agent (see scripts/test_workflow.py).
  * Safe by default: `plan`/`graph`/`validate` never execute anything, and `run`
    defaults to a dry-run echo executor unless `--execute` is passed.

Spec format (JSON):
{
  "name": "feature",
  "description": "...",
  "vars": { "branch": "main" },              # optional defaults for {vars.x}
  "stages": [
    { "name": "plan",  "agent": "alfred-planner", "task": "Break down: {task}",
      "depends_on": [] },
    { "name": "code",  "agent": "alfred-coder",   "task": "Implement:\n{stage.plan}",
      "depends_on": ["plan"] },
    { "name": "review","agent": "alfred-reviewer","task": "Review.\n{deps}",
      "depends_on": ["code"],
      "loop_to": { "target": "code", "trigger": "NEEDS_CHANGES", "max_iterations": 3 } }
  ]
}

Task placeholders: {task} (overall objective), {deps} (all dependency outputs
concatenated), {stage.<name>} (one stage's output), {vars.<key>}.

CLI:
  python scripts/workflow.py validate  <spec.json>
  python scripts/workflow.py plan      <spec.json>
  python scripts/workflow.py graph     <spec.json>          # Mermaid diagram
  python scripts/workflow.py run       <spec.json> --task "..." [--execute] [--var k=v]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(REPO_ROOT, ".kiro", "agents")


class WorkflowError(Exception):
    """Raised for any invalid workflow spec (validation failure)."""


# --------------------------------------------------------------------------- #
# Loading + validation (pure, testable)
# --------------------------------------------------------------------------- #
def load_spec(path):
    """Read a workflow spec from a JSON file and validate its shape."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except FileNotFoundError:
        raise WorkflowError(f"spec not found: {path}")
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"spec is not valid JSON: {exc}")
    validate_spec(spec)
    return spec


def validate_spec(spec, known_agents=None):
    """Validate a workflow spec dict. Raises WorkflowError on the first problem.

    known_agents: optional iterable of registered agent names. When provided,
    every stage.agent must be a member (used by `validate --check-agents`).
    """
    if not isinstance(spec, dict):
        raise WorkflowError("spec must be a JSON object")
    if not spec.get("name"):
        raise WorkflowError("spec.name is required")
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        raise WorkflowError("spec.stages must be a non-empty list")

    names = []
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            raise WorkflowError(f"stage #{i} must be an object")
        for field in ("name", "agent", "task"):
            if not st.get(field):
                raise WorkflowError(f"stage #{i} is missing required field '{field}'")
        names.append(st["name"])

    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise WorkflowError(f"duplicate stage name(s): {', '.join(dupes)}")

    nameset = set(names)
    for st in stages:
        for dep in st.get("depends_on", []) or []:
            if dep == st["name"]:
                raise WorkflowError(f"stage '{st['name']}' depends on itself")
            if dep not in nameset:
                raise WorkflowError(
                    f"stage '{st['name']}' depends on unknown stage '{dep}'"
                )
        loop = st.get("loop_to")
        if loop:
            tgt = loop.get("target")
            if tgt not in nameset:
                raise WorkflowError(
                    f"stage '{st['name']}' loops to unknown stage '{tgt}'"
                )
            if not loop.get("trigger"):
                raise WorkflowError(f"stage '{st['name']}' loop_to needs a 'trigger'")
            if int(loop.get("max_iterations", 0)) < 1:
                raise WorkflowError(
                    f"stage '{st['name']}' loop_to needs max_iterations >= 1"
                )
        if known_agents is not None and st["agent"] not in known_agents:
            raise WorkflowError(
                f"stage '{st['name']}' uses unregistered agent '{st['agent']}'"
            )

    # Acyclicity is checked over depends_on edges only; loop_to edges are
    # deliberate runtime back-edges and are excluded.
    topo_order(stages)
    return True


# --------------------------------------------------------------------------- #
# Graph algorithms (pure, testable)
# --------------------------------------------------------------------------- #
def _adjacency(stages):
    deps = {st["name"]: list(st.get("depends_on", []) or []) for st in stages}
    return deps


def topo_order(stages):
    """Return stage names in a valid topological order (Kahn's algorithm).

    Raises WorkflowError naming the members of a cycle if one exists.
    """
    deps = _adjacency(stages)
    indeg = {n: len(d) for n, d in deps.items()}
    dependents = {n: [] for n in deps}
    for n, ds in deps.items():
        for d in ds:
            dependents[d].append(n)

    # Deterministic: process ready nodes in sorted order.
    ready = sorted([n for n, k in indeg.items() if k == 0])
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()

    if len(order) != len(deps):
        stuck = sorted(n for n in deps if n not in order)
        raise WorkflowError(f"workflow has a cycle among: {', '.join(stuck)}")
    return order


def waves(stages):
    """Group stages into parallel execution levels.

    Wave 0 = stages with no dependencies; wave k = stages whose dependencies
    all completed by wave k-1. Stages in the same wave can run in parallel.
    """
    deps = _adjacency(stages)
    order = topo_order(stages)  # also validates acyclicity
    level = {}
    for n in order:
        level[n] = 0 if not deps[n] else 1 + max(level[d] for d in deps[n])
    out = []
    for lvl in range(max(level.values(), default=-1) + 1):
        out.append(sorted([n for n, l in level.items() if l == lvl]))
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_task(stage, spec, overall_task, outputs, extra_vars=None):
    """Render a stage's task template with objective, deps, and vars."""
    task = stage["task"]
    dep_names = stage.get("depends_on", []) or []
    dep_blob = "\n\n".join(
        f"### From {d}:\n{outputs.get(d, '(no output captured)')}" for d in dep_names
    )
    task = task.replace("{task}", overall_task or "")
    task = task.replace("{deps}", dep_blob)
    for name, val in outputs.items():
        task = task.replace("{stage.%s}" % name, val)
    merged_vars = dict(spec.get("vars", {}) or {})
    merged_vars.update(extra_vars or {})
    for key, val in merged_vars.items():
        task = task.replace("{vars.%s}" % key, str(val))
    return task


# --------------------------------------------------------------------------- #
# Executors
# --------------------------------------------------------------------------- #
def echo_executor(agent, task):
    """Dry executor: describes what WOULD run. Never spawns an agent."""
    preview = task if len(task) <= 400 else task[:400] + " ...[truncated]"
    return f"[DRY-RUN] would run agent '{agent}' with task:\n{preview}"


def kiro_executor(agent, task):
    """Live executor: runs a stage via `kiro-cli chat --no-interactive`."""
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
           "--agent", agent, task]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    except FileNotFoundError:
        raise WorkflowError("kiro-cli not found on PATH; use --dry-run to preview")
    if proc.returncode != 0:
        return (proc.stdout or "") + "\n[stderr]\n" + (proc.stderr or "")
    return proc.stdout or ""


# --------------------------------------------------------------------------- #
# Runner (loop-aware)
# --------------------------------------------------------------------------- #
def run_workflow(spec, overall_task, executor=echo_executor, extra_vars=None,
                 run_dir=None, logger=print):
    """Execute the workflow in topological order with bounded loop support.

    Returns a dict {stage_name: output}. loop_to re-runs the target stage (and
    everything downstream up to the looping stage) when the trigger text appears
    in the looping stage's output, bounded by max_iterations.
    """
    stages = spec["stages"]
    by_name = {st["name"]: st for st in stages}
    order = topo_order(stages)
    outputs = {}
    loop_counts = {}
    if run_dir:
        os.makedirs(run_dir, exist_ok=True)

    idx = 0
    executed = 0
    guard = 0
    max_guard = len(order) * 50 + 50  # hard safety cap against runaway loops
    while idx < len(order):
        guard += 1
        if guard > max_guard:
            logger("[workflow] safety cap reached; stopping.")
            break
        name = order[idx]
        stage = by_name[name]
        task = render_task(stage, spec, overall_task, outputs, extra_vars)
        logger(f"[workflow] -> {name} ({stage['agent']})")
        out = executor(stage["agent"], task)
        outputs[name] = out
        executed += 1
        if run_dir:
            with open(os.path.join(run_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# stage: {name}\nagent: {stage['agent']}\n\n{out}\n")

        loop = stage.get("loop_to")
        if loop and loop["trigger"] in (out or ""):
            key = name
            loop_counts[key] = loop_counts.get(key, 0) + 1
            if loop_counts[key] <= int(loop["max_iterations"]):
                target = loop["target"]
                logger(f"[workflow] loop: '{name}' hit '{loop['trigger']}' -> "
                       f"back to '{target}' (iter {loop_counts[key]})")
                idx = order.index(target)
                continue
            logger(f"[workflow] loop bound reached at '{name}'; continuing.")
        idx += 1

    if run_dir:
        summary = {
            "workflow": spec["name"],
            "task": overall_task,
            "stages_executed": executed,
            "loops": loop_counts,
            "finished": datetime.now(timezone.utc).isoformat(),
        }
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    return outputs


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def format_plan(spec):
    lines = [f"workflow: {spec['name']}"]
    if spec.get("description"):
        lines.append(f"  {spec['description']}")
    for i, wave in enumerate(waves(spec["stages"])):
        by_name = {st["name"]: st for st in spec["stages"]}
        tag = "parallel" if len(wave) > 1 else "single"
        lines.append(f"\nwave {i} ({tag}):")
        for n in wave:
            st = by_name[n]
            dep = ", ".join(st.get("depends_on", []) or []) or "-"
            loop = st.get("loop_to")
            loop_s = f"  [loop->{loop['target']} on '{loop['trigger']}']" if loop else ""
            lines.append(f"  - {n:<14} {st['agent']:<22} deps: {dep}{loop_s}")
    return "\n".join(lines)


def format_mermaid(spec):
    lines = ["```mermaid", "flowchart TD"]
    for st in spec["stages"]:
        node = st["name"]
        label = f"{node}<br/>{st['agent']}"
        lines.append(f'  {node}["{label}"]')
    for st in spec["stages"]:
        for dep in st.get("depends_on", []) or []:
            lines.append(f"  {dep} --> {st['name']}")
        loop = st.get("loop_to")
        if loop:
            lines.append(f"  {st['name']} -. {loop['trigger']} .-> {loop['target']}")
    lines.append("```")
    return "\n".join(lines)


def _known_agents():
    if not os.path.isdir(AGENTS_DIR):
        return None
    names = set()
    for fn in os.listdir(AGENTS_DIR):
        if fn.endswith(".json"):
            names.add(fn[:-5])
    return names or None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="Alfred workflow engine (DAG runner)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="validate a workflow spec")
    p_val.add_argument("spec")
    p_val.add_argument("--check-agents", action="store_true",
                       help="also require each stage.agent to be registered")

    p_plan = sub.add_parser("plan", help="print the parallel execution plan")
    p_plan.add_argument("spec")

    p_graph = sub.add_parser("graph", help="print a Mermaid diagram of the DAG")
    p_graph.add_argument("spec")

    p_run = sub.add_parser("run", help="execute the workflow")
    p_run.add_argument("spec")
    p_run.add_argument("--task", default="", help="the overall objective")
    p_run.add_argument("--execute", action="store_true",
                       help="really run agents (default is a safe dry run)")
    p_run.add_argument("--var", action="append", default=[],
                       help="k=v override for {vars.k}; repeatable")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "validate":
            spec = load_spec(args.spec)
            if args.check_agents:
                validate_spec(spec, known_agents=_known_agents())
            print(f"OK: '{spec['name']}' is valid "
                  f"({len(spec['stages'])} stages, {len(waves(spec['stages']))} waves).")
            return 0

        if args.cmd == "plan":
            print(format_plan(load_spec(args.spec)))
            return 0

        if args.cmd == "graph":
            print(format_mermaid(load_spec(args.spec)))
            return 0

        if args.cmd == "run":
            spec = load_spec(args.spec)
            extra = {}
            for kv in args.var:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    extra[k] = v
            executor = kiro_executor if args.execute else echo_executor
            run_dir = None
            if args.execute:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                run_dir = os.path.join(REPO_ROOT, "memory", "workflows",
                                       f"{spec['name']}-{stamp}")
            mode = "EXECUTE" if args.execute else "DRY-RUN"
            print(f"[workflow] {mode}: {spec['name']}")
            print(format_plan(spec))
            print("-" * 60)
            outputs = run_workflow(spec, args.task, executor=executor,
                                   extra_vars=extra, run_dir=run_dir)
            print("-" * 60)
            print(f"[workflow] done: {len(outputs)} stage(s) executed.")
            if run_dir:
                print(f"[workflow] artifacts: {run_dir}")
            return 0
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
