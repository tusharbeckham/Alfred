#!/usr/bin/env python3
"""Tests for the Alfred workflow engine (scripts/workflow.py).

Pure-logic coverage - no agents are spawned. Runs standalone:
    python scripts/test_workflow.py
or under pytest:
    python -m pytest scripts/test_workflow.py
"""
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workflow as wf  # noqa: E402


def feature_spec():
    return {
        "name": "feature",
        "stages": [
            {"name": "plan", "agent": "alfred-planner", "task": "plan: {task}",
             "depends_on": []},
            {"name": "code", "agent": "alfred-coder", "task": "code:\n{stage.plan}",
             "depends_on": ["plan"]},
            {"name": "research", "agent": "alfred-researcher", "task": "research: {task}",
             "depends_on": ["plan"]},
            {"name": "test", "agent": "alfred-tester", "task": "test\n{deps}",
             "depends_on": ["code"]},
            {"name": "review", "agent": "alfred-reviewer", "task": "review\n{deps}",
             "depends_on": ["test", "research"]},
        ],
    }


class _FixedRng:
    """Deterministic rng: uniform always returns 0 (no jitter)."""
    @staticmethod
    def uniform(a, b):
        return 0.0


class ValidationTests(unittest.TestCase):
    def test_valid_spec_passes(self):
        self.assertTrue(wf.validate_spec(feature_spec()))

    def test_missing_name(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec({"stages": [{"name": "a", "agent": "x", "task": "t"}]})

    def test_empty_stages(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec({"name": "w", "stages": []})

    def test_missing_stage_field(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec({"name": "w", "stages": [{"name": "a", "agent": "x"}]})

    def test_duplicate_stage_names(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t"},
            {"name": "a", "agent": "y", "task": "t"},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_unknown_dependency(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "depends_on": ["ghost"]},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_self_dependency(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "depends_on": ["a"]},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_cycle_detected(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "depends_on": ["b"]},
            {"name": "b", "agent": "x", "task": "t", "depends_on": ["a"]},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_loop_to_unknown_target(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t",
             "loop_to": {"target": "ghost", "trigger": "X", "max_iterations": 2}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_loop_to_bad_iterations(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t"},
            {"name": "b", "agent": "y", "task": "t", "depends_on": ["a"],
             "loop_to": {"target": "a", "trigger": "X", "max_iterations": 0}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_loop_negative_backoff(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t"},
            {"name": "b", "agent": "y", "task": "t", "depends_on": ["a"],
             "loop_to": {"target": "a", "trigger": "X", "max_iterations": 2,
                         "backoff": -1}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_bad_timeout(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "timeout": 0},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_bad_budget(self):
        spec = {"name": "w", "budget": 0,
                "stages": [{"name": "a", "agent": "x", "task": "t"}]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_when_missing_stage(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t"},
            {"name": "b", "agent": "y", "task": "t", "depends_on": ["a"],
             "when": {"contains": "GO"}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_when_unknown_stage(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t",
             "when": {"stage": "ghost", "contains": "GO"}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_when_missing_predicate(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t"},
            {"name": "b", "agent": "y", "task": "t", "depends_on": ["a"],
             "when": {"stage": "a"}},
        ]}
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(spec)

    def test_check_agents_flags_unregistered(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(feature_spec(), known_agents={"alfred-planner"})


class GraphTests(unittest.TestCase):
    def test_topo_order_respects_dependencies(self):
        order = wf.topo_order(feature_spec()["stages"])
        self.assertLess(order.index("plan"), order.index("code"))
        self.assertLess(order.index("code"), order.index("test"))
        self.assertLess(order.index("test"), order.index("review"))
        self.assertLess(order.index("research"), order.index("review"))

    def test_waves_group_parallel_stages(self):
        w = wf.waves(feature_spec()["stages"])
        self.assertEqual(w[0], ["plan"])
        self.assertEqual(w[1], ["code", "research"])  # parallel
        self.assertEqual(w[-1], ["review"])           # fan-in

    def test_waves_single_stage(self):
        spec = {"name": "w", "stages": [{"name": "a", "agent": "x", "task": "t"}]}
        self.assertEqual(wf.waves(spec["stages"]), [["a"]])


class RenderTests(unittest.TestCase):
    def test_task_placeholder(self):
        st = {"name": "a", "agent": "x", "task": "do {task}", "depends_on": []}
        out = wf.render_task(st, {"name": "w"}, "BUILD", {})
        self.assertEqual(out, "do BUILD")

    def test_stage_and_deps_placeholders(self):
        st = {"name": "b", "agent": "x", "task": "{stage.a} || {deps}",
              "depends_on": ["a"]}
        out = wf.render_task(st, {"name": "w"}, "", {"a": "RESULT_A"})
        self.assertIn("RESULT_A", out)
        self.assertIn("From a", out)

    def test_vars_placeholder(self):
        st = {"name": "a", "agent": "x", "task": "branch={vars.branch}"}
        out = wf.render_task(st, {"name": "w", "vars": {"branch": "dev"}}, "", {})
        self.assertEqual(out, "branch=dev")


class ConditionTests(unittest.TestCase):
    def test_evaluate_when_none_runs(self):
        self.assertTrue(wf.evaluate_when({"name": "a"}, {}))

    def test_evaluate_when_contains(self):
        st = {"name": "b", "when": {"stage": "a", "contains": "GO"}}
        self.assertTrue(wf.evaluate_when(st, {"a": "we GO now"}))
        self.assertFalse(wf.evaluate_when(st, {"a": "no"}))

    def test_evaluate_when_not_contains(self):
        st = {"name": "b", "when": {"stage": "a", "not_contains": "SKIP"}}
        self.assertTrue(wf.evaluate_when(st, {"a": "ok"}))
        self.assertFalse(wf.evaluate_when(st, {"a": "please SKIP"}))

    def test_run_skips_gated_stage(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "c", "task": "t"},
            {"name": "b", "agent": "d", "task": "t", "depends_on": ["a"],
             "when": {"stage": "a", "contains": "GO"}},
        ]}
        calls = []

        def ex(agent, task, timeout=None):
            calls.append(agent)
            return "STOP"  # 'a' returns STOP -> 'b' gate fails

        wf.run_workflow(spec, "", executor=ex, logger=lambda *_: None)
        self.assertEqual(calls, ["c"])  # only 'a' ran; 'b' was skipped


class RunTests(unittest.TestCase):
    def test_dry_run_executes_every_stage_once(self):
        seen = []

        def rec(agent, task, timeout=None):
            seen.append(agent)
            return "ok"

        wf.run_workflow(feature_spec(), "obj", executor=rec, logger=lambda *_: None)
        self.assertEqual(len(seen), 5)

    def test_loop_reruns_target_until_bound(self):
        spec = {"name": "w", "stages": [
            {"name": "code", "agent": "c", "task": "t"},
            {"name": "review", "agent": "r", "task": "t", "depends_on": ["code"],
             "loop_to": {"target": "code", "trigger": "NEEDS_CHANGES",
                         "max_iterations": 2}},
        ]}
        calls = {"code": 0, "review": 0}

        def executor(agent, task, timeout=None):
            name = "code" if agent == "c" else "review"
            calls[name] += 1
            return "NEEDS_CHANGES"  # always trips the loop

        wf.run_workflow(spec, "obj", executor=executor, logger=lambda *_: None)
        self.assertEqual(calls["code"], 3)
        self.assertEqual(calls["review"], 3)

    def test_loop_not_triggered_when_no_trigger_text(self):
        spec = {"name": "w", "stages": [
            {"name": "code", "agent": "c", "task": "t"},
            {"name": "review", "agent": "r", "task": "t", "depends_on": ["code"],
             "loop_to": {"target": "code", "trigger": "NEEDS_CHANGES",
                         "max_iterations": 3}},
        ]}
        calls = {"n": 0}

        def executor(agent, task, timeout=None):
            calls["n"] += 1
            return "all good"

        wf.run_workflow(spec, "obj", executor=executor, logger=lambda *_: None)
        self.assertEqual(calls["n"], 2)  # each stage once, no loop

    def test_budget_caps_executions(self):
        # linear chain of 5; budget 2 -> only 2 stages execute
        stages = [{"name": f"s{i}", "agent": "a", "task": "t",
                   "depends_on": ([f"s{i-1}"] if i else [])} for i in range(5)]
        spec = {"name": "w", "stages": stages}
        n = {"c": 0}

        def ex(agent, task, timeout=None):
            n["c"] += 1
            return "ok"

        wf.run_workflow(spec, "", executor=ex, logger=lambda *_: None, budget=2)
        self.assertEqual(n["c"], 2)

    def test_timeout_passed_to_executor(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "timeout": 42},
        ]}
        seen = {}

        def ex(agent, task, timeout=None):
            seen["t"] = timeout
            return "ok"

        wf.run_workflow(spec, "", executor=ex, logger=lambda *_: None)
        self.assertEqual(seen["t"], 42)


class BackoffTests(unittest.TestCase):
    def test_backoff_zero_base(self):
        self.assertEqual(wf.backoff_delay(0, 1), 0.0)

    def test_backoff_exponential_no_jitter(self):
        self.assertEqual(wf.backoff_delay(2, 1, rng=_FixedRng), 2.0)
        self.assertEqual(wf.backoff_delay(2, 2, rng=_FixedRng), 4.0)
        self.assertEqual(wf.backoff_delay(2, 3, rng=_FixedRng), 8.0)

    def test_run_sleeps_on_backoff_loop(self):
        spec = {"name": "w", "stages": [
            {"name": "code", "agent": "c", "task": "t"},
            {"name": "review", "agent": "r", "task": "t", "depends_on": ["code"],
             "loop_to": {"target": "code", "trigger": "X", "max_iterations": 2,
                         "backoff": 1}},
        ]}
        delays = []
        wf.run_workflow(spec, "", executor=lambda a, t, timeout=None: "X",
                        logger=lambda *_: None, sleeper=delays.append)
        self.assertEqual(len(delays), 2)          # one sleep per loop retry
        self.assertTrue(all(d > 0 for d in delays))


class HistoryTests(unittest.TestCase):
    def test_run_writes_run_json(self):
        with tempfile.TemporaryDirectory() as d:
            wf.run_workflow(feature_spec(), "obj",
                            executor=lambda a, t, timeout=None: "ok",
                            logger=lambda *_: None, run_dir=d)
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["stages_executed"], 5)
            self.assertEqual(len(data["records"]), 5)

    def test_list_and_format_runs(self):
        with tempfile.TemporaryDirectory() as base:
            rd = os.path.join(base, "feature-20260718-000000")
            os.makedirs(rd)
            with open(os.path.join(rd, "run.json"), "w", encoding="utf-8") as fh:
                json.dump({"workflow": "feature", "finished": "2026-07-18T00:00:00",
                           "stages_executed": 3, "skipped": []}, fh)
            runs = wf.list_runs(base_dir=base, limit=5)
            self.assertEqual(len(runs), 1)
            self.assertIn("feature", wf.format_runs(runs))


class ResumeTests(unittest.TestCase):
    @staticmethod
    def _write_run(d, records, outputs):
        os.makedirs(d, exist_ok=True)
        for name, out in outputs.items():
            with open(os.path.join(d, f"{name}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# stage: {name}\nagent: x\n\n{out}\n")
        with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
            json.dump({"records": records}, fh)

    def _chain(self):
        return {"name": "w", "stages": [
            {"name": "a", "agent": "a", "task": "t"},
            {"name": "b", "agent": "b", "task": "t", "depends_on": ["a"]},
            {"name": "c", "agent": "c", "task": "t", "depends_on": ["b"]},
        ]}

    def test_load_completed_reads_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_run(d, [{"stage": "a", "status": "ok"}], {"a": "OUT_A"})
            done = wf._load_completed(d)
            self.assertEqual(done, {"a": "OUT_A"})

    def test_missing_run_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(wf.WorkflowError):
                wf._load_completed(d)

    def test_resume_skips_completed_stages(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_run(d,
                            [{"stage": "a", "status": "ok"}, {"stage": "b", "status": "ok"}],
                            {"a": "A", "b": "B"})
            ran = []
            wf.run_workflow(self._chain(), "", executor=lambda ag, t, timeout=None: ran.append(ag) or "ok",
                            logger=lambda *_: None, resume_from=d)
            self.assertEqual(ran, ["c"])            # only the unfinished stage ran

    def test_resume_reexecutes_non_ok_stage(self):
        with tempfile.TemporaryDirectory() as d:
            # 'a' finished; 'b' timed out last time -> b and c must run again
            self._write_run(d,
                            [{"stage": "a", "status": "ok"}, {"stage": "b", "status": "timeout"}],
                            {"a": "A"})
            ran = []
            wf.run_workflow(self._chain(), "", executor=lambda ag, t, timeout=None: ran.append(ag) or "ok",
                            logger=lambda *_: None, resume_from=d)
            self.assertEqual(ran, ["b", "c"])       # 'a' cached, rest re-run


class PresentationTests(unittest.TestCase):
    def test_plan_mentions_waves(self):
        self.assertIn("wave 0", wf.format_plan(feature_spec()))

    def test_mermaid_has_edges(self):
        m = wf.format_mermaid(feature_spec())
        self.assertIn("flowchart TD", m)
        self.assertIn("plan --> code", m)


class ParallelTests(unittest.TestCase):
    """Wave-parallel execution: concurrency, determinism, and the max_parallel=1
    escape hatch that must stay bit-for-bit identical to the sequential path."""

    def _diamond(self):
        # a -> {b, c} -> d : b and c share a wave and may run concurrently.
        return {"name": "w", "stages": [
            {"name": "a", "agent": "a", "task": "t"},
            {"name": "b", "agent": "b", "task": "t", "depends_on": ["a"]},
            {"name": "c", "agent": "c", "task": "t", "depends_on": ["a"]},
            {"name": "d", "agent": "d", "task": "t", "depends_on": ["b", "c"]},
        ]}

    def test_same_wave_stages_run_concurrently(self):
        # b and c must overlap in time when max_parallel allows it. Each blocks on
        # a barrier that only releases once both have entered - so a *sequential*
        # runner would deadlock, proving genuine concurrency.
        started = threading.Barrier(2, timeout=5)
        overlapped = {"b": False, "c": False}

        def ex(agent, task, timeout=None):
            if agent in ("b", "c"):
                try:
                    started.wait()
                    overlapped[agent] = True
                except threading.BrokenBarrierError:
                    pass
            return "ok"

        wf.run_workflow(self._diamond(), "", executor=ex,
                        logger=lambda *_: None, max_parallel=4)
        self.assertTrue(overlapped["b"] and overlapped["c"])

    def test_results_are_deterministic_regardless_of_finish_order(self):
        # c finishes before b, but run.json records must stay in wave (name) order.
        def ex(agent, task, timeout=None):
            if agent == "b":
                time.sleep(0.05)
            return f"out-{agent}"

        with tempfile.TemporaryDirectory() as d:
            wf.run_workflow(self._diamond(), "", executor=ex,
                            logger=lambda *_: None, max_parallel=4, run_dir=d)
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        order = [r["stage"] for r in data["records"]]
        self.assertLess(order.index("b"), order.index("c"))  # sorted, not by finish

    def test_max_parallel_one_matches_default(self):
        # The sequential escape hatch must produce identical outputs to a parallel
        # run for a deterministic executor.
        def ex(agent, task, timeout=None):
            return f"out-{agent}"

        seq = wf.run_workflow(self._diamond(), "", executor=ex,
                              logger=lambda *_: None, max_parallel=1)
        par = wf.run_workflow(self._diamond(), "", executor=ex,
                              logger=lambda *_: None, max_parallel=4)
        self.assertEqual(seq, par)

    def test_max_parallel_floors_at_one(self):
        # 0 / negative must clamp to 1, never disable execution.
        seen = []
        wf.run_workflow(self._diamond(), "", executor=lambda a, t, timeout=None: seen.append(a) or "ok",
                        logger=lambda *_: None, max_parallel=0)
        self.assertEqual(sorted(seen), ["a", "b", "c", "d"])


class RetryTests(unittest.TestCase):
    """Per-stage `retries`: attempts = retries+1, backoff between tries, and the
    stage still succeeds if a later attempt does."""

    def test_retries_until_success(self):
        # Fails [ERROR] twice, then succeeds on attempt 3 (retries=2 -> 3 attempts).
        calls = {"n": 0}

        def ex(agent, task, timeout=None):
            calls["n"] += 1
            return "ok" if calls["n"] >= 3 else "[ERROR] transient"

        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "retries": 2},
        ]}
        out = wf.run_workflow(spec, "", executor=ex, logger=lambda *_: None,
                              sleeper=lambda *_: None)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(out["a"], "ok")

    def test_retries_exhausted_keeps_last_error(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "retries": 1},
        ]}
        n = {"c": 0}

        def ex(agent, task, timeout=None):
            n["c"] += 1
            return "[ERROR] still broken"

        out = wf.run_workflow(spec, "", executor=ex, logger=lambda *_: None,
                              sleeper=lambda *_: None)
        self.assertEqual(n["c"], 2)  # 1 retry -> 2 attempts
        self.assertTrue(out["a"].startswith("[ERROR]"))

    def test_retry_sleeps_between_attempts(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "x", "task": "t", "retries": 2,
             "retry_backoff": 1},
        ]}
        delays = []
        wf.run_workflow(spec, "", executor=lambda a, t, timeout=None: "[ERROR] x",
                        logger=lambda *_: None, sleeper=delays.append)
        self.assertEqual(len(delays), 2)  # a sleep after attempts 1 and 2, none after 3
        self.assertTrue(all(d > 0 for d in delays))

    def test_executor_exception_becomes_error_output(self):
        # A backend blowing up must not crash the run; it becomes an [ERROR] output.
        def boom(agent, task, timeout=None):
            raise RuntimeError("backend exploded")

        spec = {"name": "w", "stages": [{"name": "a", "agent": "x", "task": "t"}]}
        out = wf.run_workflow(spec, "", executor=boom, logger=lambda *_: None)
        self.assertTrue(out["a"].startswith("[ERROR]"))
        self.assertIn("backend exploded", out["a"])


class OnErrorTests(unittest.TestCase):
    """`on_error: fail` aborts the run; the default carries the error forward."""

    def _spec(self, on_error=None):
        first = {"name": "a", "agent": "a", "task": "t"}
        if on_error:
            first["on_error"] = on_error
        return {"name": "w", "stages": [
            first,
            {"name": "b", "agent": "b", "task": "t", "depends_on": ["a"]},
        ]}

    def test_on_error_fail_aborts_downstream(self):
        ran = []

        def ex(agent, task, timeout=None):
            ran.append(agent)
            return "[ERROR] boom" if agent == "a" else "ok"

        with tempfile.TemporaryDirectory() as d:
            wf.run_workflow(self._spec("fail"), "", executor=ex,
                            logger=lambda *_: None, run_dir=d)
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(ran, ["a"])         # b never ran
        self.assertIsNotNone(data["aborted"])

    def test_default_carries_error_forward(self):
        ran = []

        def ex(agent, task, timeout=None):
            ran.append(agent)
            return "[ERROR] boom" if agent == "a" else "ok"

        wf.run_workflow(self._spec(), "", executor=ex, logger=lambda *_: None)
        self.assertEqual(ran, ["a", "b"])    # no on_error -> b still runs

    def test_validate_rejects_unknown_on_error(self):
        with self.assertRaises(wf.WorkflowError):
            wf.validate_spec(self._spec("explode"))


class CostAccountingTests(unittest.TestCase):
    """Per-stage cost from executor.last_meta is recorded and summed into run.json."""

    class _MeteredExecutor:
        """Executor that reports a per-call cost via the last_meta protocol."""
        def __init__(self, cost):
            self.cost = cost
            self.last_meta = {}

        def __call__(self, agent, task, timeout=None):
            self.last_meta = {"backend": "api", "model": "claude-opus-5",
                              "cost_usd": self.cost}
            return "ok"

    def test_total_cost_is_summed(self):
        spec = {"name": "w", "stages": [
            {"name": "a", "agent": "a", "task": "t"},
            {"name": "b", "agent": "b", "task": "t", "depends_on": ["a"]},
            {"name": "c", "agent": "c", "task": "t", "depends_on": ["b"]},
        ]}
        with tempfile.TemporaryDirectory() as d:
            wf.run_workflow(spec, "", executor=self._MeteredExecutor(0.25),
                            logger=lambda *_: None, run_dir=d)
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertAlmostEqual(data["cost_usd"], 0.75, places=6)
        self.assertTrue(all(r["cost_usd"] == 0.25 for r in data["records"]))
        self.assertEqual(data["records"][0]["backend"], "api")

    def test_missing_cost_sums_to_zero(self):
        # The echo executor reports no cost; run.json still gets a numeric total.
        with tempfile.TemporaryDirectory() as d:
            wf.run_workflow(feature_spec(), "obj",
                            executor=lambda a, t, timeout=None: "ok",
                            logger=lambda *_: None, run_dir=d)
            with open(os.path.join(d, "run.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(data["cost_usd"], 0.0)


def _load_sync_module():
    """Import the OPTIONAL Claude Code layer generator.

    `sync-claude-config.py` and the `.claude/` tree it generates belong to the
    Claude Code front end, not to the workflow engine. The engine must test
    green without them, so treat the generator as optional: return None when it
    is absent and let the drift gate skip rather than error.
    """
    try:
        # sync-claude-config.py has a hyphenated name; import it via importlib.
        return importlib.import_module("sync-claude-config")
    except ImportError:
        return None


_SYNC = _load_sync_module()


@unittest.skipIf(_SYNC is None,
                 "Claude Code layer not present (scripts/sync-claude-config.py absent)")
class SyncDriftTests(unittest.TestCase):
    """The CI gate: sync-claude-config must render deterministically and its
    on-disk output must already be in sync (no uncommitted drift).

    Skipped automatically when the Claude Code front end is not checked out.
    """

    def setUp(self):
        self.sync = _SYNC

    def test_render_all_is_pure_and_deterministic(self):
        a = self.sync.render_all()
        b = self.sync.render_all()
        self.assertEqual(a, b)
        self.assertTrue(a)  # renders at least one file

    def test_generated_files_carry_banner(self):
        rendered = self.sync.render_all()
        claude_md = rendered["CLAUDE.md"]
        self.assertIn("GENERATED by scripts/sync-claude-config.py", claude_md)

    def test_disk_is_in_sync(self):
        # Equivalent to `sync-claude-config.py --check`: nothing stale on disk.
        bad = self.sync.stale(self.sync.render_all())
        self.assertEqual(bad, [], f"stale generated files: {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
