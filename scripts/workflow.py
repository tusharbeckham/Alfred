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

These production-workflow patterns are borrowed from engines like Argo/Temporal
(see docs/orchestration/kubernetes-decision.md - "adopt patterns, not platform"):
  * bounded loops with exponential **backoff + jitter** (loop_to.backoff)
  * per-stage **timeout** (stage.timeout, seconds)
  * a per-run **budget** on total stage executions (spec.budget or --budget)
  * **conditional** stages that skip unless a dependency's output matches (when)
  * **run history** written to memory/workflows and surfaced by `runs`

Spec format (JSON):
{
  "name": "feature",
  "description": "...",
  "vars": { "branch": "main" },              # optional defaults for {vars.x}
  "budget": 20,                               # optional max stage executions
  "stages": [
    { "name": "plan",  "agent": "alfred-planner", "task": "Break down: {task}",
      "depends_on": [] },
    { "name": "code",  "agent": "alfred-coder",   "task": "Implement:\n{stage.plan}",
      "depends_on": ["plan"], "timeout": 900 },
    { "name": "ship",  "agent": "alfred-devops",  "task": "Ship it.\n{deps}",
      "depends_on": ["review"],
      "when": { "stage": "review", "contains": "APPROVED" } },
    { "name": "review","agent": "alfred-reviewer","task": "Review.\n{deps}",
      "depends_on": ["code"],
      "loop_to": { "target": "code", "trigger": "NEEDS_CHANGES",
                   "max_iterations": 3, "backoff": 2 } }
  ]
}

Task placeholders: {task} (overall objective), {deps} (all dependency outputs
concatenated), {stage.<name>} (one stage's output), {vars.<key>}.

CLI:
  python scripts/workflow.py validate  <spec.json> [--check-agents]
  python scripts/workflow.py plan      <spec.json>
  python scripts/workflow.py graph     <spec.json>          # Mermaid diagram
  python scripts/workflow.py run       <spec.json> --task "..." [--execute] [--budget N] [--var k=v]
  python scripts/workflow.py runs      [--limit N]          # recent run history
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(REPO_ROOT, ".kiro", "agents")
RUNS_DIR = os.path.join(REPO_ROOT, "memory", "workflows")


class WorkflowError(Exception):
    """Raised for any invalid workflow spec (validation failure)."""


# --------------------------------------------------------------------------- #
# Loading + validation (pure, testable)
# --------------------------------------------------------------------------- #
def load_spec(path):
    """Read a workflow spec from a JSON file and validate its shape."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
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
    if "budget" in spec and int(spec["budget"]) < 1:
        raise WorkflowError("spec.budget must be >= 1")

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
            if float(loop.get("backoff", 0)) < 0:
                raise WorkflowError(f"stage '{st['name']}' loop_to backoff must be >= 0")
        if "timeout" in st and float(st["timeout"]) <= 0:
            raise WorkflowError(f"stage '{st['name']}' timeout must be > 0")
        cond = st.get("when")
        if cond is not None:
            if not isinstance(cond, dict) or not cond.get("stage"):
                raise WorkflowError(f"stage '{st['name']}' when needs a 'stage'")
            if cond["stage"] not in nameset:
                raise WorkflowError(
                    f"stage '{st['name']}' when references unknown stage '{cond['stage']}'"
                )
            if "contains" not in cond and "not_contains" not in cond:
                raise WorkflowError(
                    f"stage '{st['name']}' when needs 'contains' or 'not_contains'"
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
# Rendering + conditions (pure, testable)
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


def evaluate_when(stage, outputs):
    """Return True if the stage should run. A `when` gates on a prior output."""
    cond = stage.get("when")
    if not cond:
        return True
    src = outputs.get(cond["stage"], "")
    if "contains" in cond:
        return cond["contains"] in (src or "")
    if "not_contains" in cond:
        return cond["not_contains"] not in (src or "")
    return True


def backoff_delay(base, attempt, rng=random):
    """Exponential backoff with full jitter: base*2^(attempt-1) + U(0, base/2)."""
    base = float(base or 0)
    if base <= 0:
        return 0.0
    return base * (2 ** (attempt - 1)) + rng.uniform(0, base / 2.0)


# --------------------------------------------------------------------------- #
# Executors
# --------------------------------------------------------------------------- #
def echo_executor(agent, task, timeout=None):
    """Dry executor: describes what WOULD run. Never spawns an agent."""
    preview = task if len(task) <= 400 else task[:400] + " ...[truncated]"
    return f"[DRY-RUN] would run agent '{agent}' with task:\n{preview}"


def kiro_executor(agent, task, timeout=None):
    """Live executor: runs a stage via `kiro-cli chat --no-interactive`."""
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
           "--agent", agent, task]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT,
                              timeout=timeout)
    except FileNotFoundError:
        raise WorkflowError("kiro-cli not found on PATH; use --dry-run to preview")
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] stage exceeded {timeout}s and was terminated."
    if proc.returncode != 0:
        return (proc.stdout or "") + "\n[stderr]\n" + (proc.stderr or "")
    return proc.stdout or ""


# --------------------------------------------------------------------------- #
# Runner (loop-aware, budgeted, conditional)
# --------------------------------------------------------------------------- #
def run_workflow(spec, overall_task, executor=echo_executor, extra_vars=None,
                 run_dir=None, logger=print, budget=None, sleeper=time.sleep):
    """Execute the workflow in topological order.

    Returns a dict {stage_name: output}. Features:
      * loop_to re-runs a target stage when the trigger appears (bounded by
        max_iterations, with optional exponential backoff+jitter between tries);
      * `when` skips a stage unless a prior output matches;
      * `budget` caps total stage executions (spec.budget or the budget arg);
      * per-stage `timeout` is passed to the executor.
    """
def run_workflow(spec, overall_task, executor=echo_executor, extra_vars=None,
                 run_dir=None, logger=print, budget=None, sleeper=time.sleep,
                 resume_from=None):
    """Execute the workflow in topological order.

    Returns a dict {stage_name: output}. Features:
      * loop_to re-runs a target stage when the trigger appears (bounded by
        max_iterations, with optional exponential backoff+jitter between tries);
      * `when` skips a stage unless a prior output matches;
      * `budget` caps total stage executions (spec.budget or the budget arg);
      * per-stage `timeout` is passed to the executor;
      * `resume_from` (a prior run dir) skips stages that already succeeded and
        re-runs only the rest - crash/failure recovery.
    """
    stages = spec["stages"]
    by_name = {st["name"]: st for st in stages}
    order = topo_order(stages)
    outputs = {}
    loop_counts = {}
    records = []
    skipped = []
    cached = _load_completed(resume_from) if resume_from else {}
    if cached:
        logger(f"[workflow] resume: {len(cached)} completed stage(s) loaded; "
               f"they will be skipped.")
    if budget is None:
        budget = spec.get("budget")
    if run_dir:
        os.makedirs(run_dir, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
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

        if name in cached:
            outputs[name] = cached[name]
            records.append({"stage": name, "agent": stage["agent"], "status": "cached"})
            logger(f"[workflow] cached {name} (resumed - skipped)")
            idx += 1
            continue

        if not evaluate_when(stage, outputs):
            logger(f"[workflow] skip {name}: 'when' condition not met.")
            outputs[name] = ""
            skipped.append(name)
            records.append({"stage": name, "agent": stage["agent"],
                            "status": "skipped"})
            idx += 1
            continue

        if budget is not None and executed >= int(budget):
            logger(f"[workflow] budget of {budget} stage-executions reached; stopping.")
            break

        task = render_task(stage, spec, overall_task, outputs, extra_vars)
        logger(f"[workflow] -> {name} ({stage['agent']})")
        t0 = time.time()
        out = executor(stage["agent"], task, timeout=stage.get("timeout"))
        ms = int((time.time() - t0) * 1000)
        outputs[name] = out
        executed += 1
        status = "timeout" if isinstance(out, str) and out.startswith("[TIMEOUT]") else "ok"
        records.append({"stage": name, "agent": stage["agent"], "status": status,
                        "ms": ms, "iteration": loop_counts.get(name, 0)})
        if run_dir:
            with open(os.path.join(run_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# stage: {name}\nagent: {stage['agent']}\n\n{out}\n")

        loop = stage.get("loop_to")
        if loop and loop["trigger"] in (out or ""):
            loop_counts[name] = loop_counts.get(name, 0) + 1
            if loop_counts[name] <= int(loop["max_iterations"]):
                target = loop["target"]
                ti = order.index(target)
                for nm in order[ti:]:      # re-entered loop segment must run fresh
                    cached.pop(nm, None)
                delay = backoff_delay(loop.get("backoff", 0), loop_counts[name])
                if delay > 0:
                    logger(f"[workflow] backoff {delay:.2f}s before retry")
                    sleeper(delay)
                logger(f"[workflow] loop: '{name}' hit '{loop['trigger']}' -> "
                       f"back to '{target}' (iter {loop_counts[name]})")
                idx = ti
                continue
            logger(f"[workflow] loop bound reached at '{name}'; continuing.")
        idx += 1

    if run_dir:
        summary = {
            "workflow": spec["name"],
            "task": overall_task,
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "stages_executed": executed,
            "skipped": skipped,
            "loops": loop_counts,
            "budget": budget,
            "resumed_from": resume_from,
            "records": records,
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
    if spec.get("budget"):
        lines.append(f"  budget: {spec['budget']} stage-executions")
    for i, wave in enumerate(waves(spec["stages"])):
        by_name = {st["name"]: st for st in spec["stages"]}
        tag = "parallel" if len(wave) > 1 else "single"
        lines.append(f"\nwave {i} ({tag}):")
        for n in wave:
            st = by_name[n]
            dep = ", ".join(st.get("depends_on", []) or []) or "-"
            extra = ""
            loop = st.get("loop_to")
            if loop:
                bo = f" backoff {loop['backoff']}s" if loop.get("backoff") else ""
                extra += f"  [loop->{loop['target']} on '{loop['trigger']}'{bo}]"
            if st.get("when"):
                extra += f"  [when {st['when']['stage']}]"
            if st.get("timeout"):
                extra += f"  [timeout {st['timeout']}s]"
            lines.append(f"  - {n:<14} {st['agent']:<22} deps: {dep}{extra}")
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


def _load_completed(run_dir):
    """Load {stage: output} for stages that completed OK in a prior run dir.

    Reads run.json for records with status 'ok' and pulls each stage's captured
    output from its <stage>.md file. Raises WorkflowError if the dir has no run.json.
    """
    done = {}
    if not run_dir:
        return done
    rj = os.path.join(run_dir, "run.json")
    if not os.path.isfile(rj):
        raise WorkflowError(f"resume: no run.json found in {run_dir}")
    try:
        with open(rj, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise WorkflowError(f"resume: cannot read run.json: {exc}")
    for rec in data.get("records", []):
        if rec.get("status") == "ok":
            name = rec["stage"]
            out = ""
            md = os.path.join(run_dir, f"{name}.md")
            if os.path.isfile(md):
                with open(md, "r", encoding="utf-8") as fh:
                    text = fh.read()
                parts = text.split("\n\n", 1)
                out = parts[1].rstrip("\n") if len(parts) == 2 else ""
            done[name] = out
    return done


def list_runs(base_dir=RUNS_DIR, limit=10):
    """Return recent run summaries (newest first) from memory/workflows/*/run.json."""
    out = []
    if not os.path.isdir(base_dir):
        return out
    for name in os.listdir(base_dir):
        rj = os.path.join(base_dir, name, "run.json")
        if os.path.isfile(rj):
            try:
                with open(rj, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data["_dir"] = name
                out.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    out.sort(key=lambda d: d.get("finished", ""), reverse=True)
    return out[:limit]


def format_runs(runs):
    if not runs:
        return "(no workflow runs recorded yet under memory/workflows/)"
    lines = [f"{'finished':<22} {'workflow':<12} {'stages':>6} {'skipped':>7}  dir",
             "-" * 72]
    for r in runs:
        lines.append("{:<22} {:<12} {:>6} {:>7}  {}".format(
            (r.get("finished", "")[:19] or "?"),
            r.get("workflow", "?"),
            r.get("stages_executed", 0),
            len(r.get("skipped", []) or []),
            r.get("_dir", "?"),
        ))
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
    p_run.add_argument("--budget", type=int, default=None,
                       help="max total stage executions for this run")
    p_run.add_argument("--resume", default=None,
                       help="prior run dir to resume from (skip stages that already succeeded)")
    p_run.add_argument("--var", action="append", default=[],
                       help="k=v override for {vars.k}; repeatable")

    p_runs = sub.add_parser("runs", help="list recent workflow runs")
    p_runs.add_argument("--limit", type=int, default=10)

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

        if args.cmd == "runs":
            print(format_runs(list_runs(limit=args.limit)))
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
                run_dir = os.path.join(RUNS_DIR, f"{spec['name']}-{stamp}")
            mode = "EXECUTE" if args.execute else "DRY-RUN"
            print(f"[workflow] {mode}: {spec['name']}")
            print(format_plan(spec))
            print("-" * 60)
            outputs = run_workflow(spec, args.task, executor=executor,
                                   extra_vars=extra, run_dir=run_dir,
                                   budget=args.budget, resume_from=args.resume)
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
