#!/usr/bin/env python3
"""Tests for the Alfred workflow engine (scripts/workflow.py).

Pure-logic coverage - no agents are spawned. Runs standalone:
    python scripts/test_workflow.py
or under pytest:
    python -m pytest scripts/test_workflow.py
"""
import os
import sys
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


class RunTests(unittest.TestCase):
    def test_dry_run_executes_every_stage_once(self):
        seen = []

        def rec(agent, task):
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

        def executor(agent, task):
            name = "code" if agent == "c" else "review"
            calls[name] += 1
            return "NEEDS_CHANGES"  # always trips the loop

        wf.run_workflow(spec, "obj", executor=executor, logger=lambda *_: None)
        # initial code + 2 loop re-runs = 3; review runs each pass = 3
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

        def executor(agent, task):
            calls["n"] += 1
            return "all good"

        wf.run_workflow(spec, "obj", executor=executor, logger=lambda *_: None)
        self.assertEqual(calls["n"], 2)  # each stage once, no loop


class PresentationTests(unittest.TestCase):
    def test_plan_mentions_waves(self):
        self.assertIn("wave 0", wf.format_plan(feature_spec()))

    def test_mermaid_has_edges(self):
        m = wf.format_mermaid(feature_spec())
        self.assertIn("flowchart TD", m)
        self.assertIn("plan --> code", m)


if __name__ == "__main__":
    unittest.main(verbosity=2)
