#!/usr/bin/env python3
"""Cross-engine parity: Alfred's Python engine and Ultron's Node engine must agree.

WHY THIS TEST EXISTS
--------------------
Alfred and Ultron are deliberately separate runtimes (Python vs Node, different
trust models). The contract between them is the `gauntlet/v1` spec. If the two
diverge, then *where* you run a graph silently changes what it is allowed to do -
and the anti-thrash guarantee becomes a property of the runtime rather than of the
spec. That is worse than having only one engine.

So this asserts:
  * every spec in workflows/ validates the same way on both engines
  * the router makes the same decision for the same verdict + ledger state
  * the ladder bounds are numerically identical

Skips cleanly if Node or the Ultron checkout is absent, rather than failing a
build for an environment reason.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gauntlet as g  # noqa: E402

ULTRON = Path("C:/projects/ultron-cli")
CHECKER = ULTRON / "scripts" / "gauntlet-check.mjs"
NODE = shutil.which("node")


def ultron(command: str, payload: dict) -> dict:
    """Run Ultron's engine on a payload and return its verdict as a dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump(payload, handle)
        temp = Path(handle.name)
    try:
        proc = subprocess.run(
            [NODE, str(CHECKER), command, str(temp)],
            cwd=str(ULTRON), capture_output=True, text=True, timeout=60, shell=False,
        )
        if proc.returncode != 0:
            raise AssertionError(f"ultron {command} failed: {proc.stderr.strip()[:300]}")
        return json.loads(proc.stdout)
    finally:
        temp.unlink(missing_ok=True)


@unittest.skipIf(NODE is None, "node is not installed")
@unittest.skipUnless(CHECKER.exists(), f"ultron checkout not found at {ULTRON}")
class SpecValidationParity(unittest.TestCase):
    """The same spec file must be valid (or invalid) on both engines."""

    def assert_agrees(self, spec: dict, label: str) -> None:
        mine = g.validate_spec(spec)
        theirs = ultron("validate", spec)
        self.assertEqual(
            not mine, theirs["valid"],
            f"{label}: alfred={'valid' if not mine else mine} ultron={theirs['errors']}",
        )
        self.assertEqual(
            len(mine), theirs["count"],
            f"{label}: error count differs - alfred={mine} ultron={theirs['errors']}",
        )

    def test_every_shipped_spec_agrees(self):
        specs = sorted((ROOT / "workflows").glob("*.json"))
        self.assertTrue(specs, "no workflow specs found")
        for path in specs:
            with self.subTest(spec=path.name):
                self.assert_agrees(json.loads(path.read_text(encoding="utf-8")), path.name)

    def test_a_legacy_spec_is_untouched_by_both(self):
        self.assert_agrees({"stages": [{"name": "a", "agent": "x"}]}, "legacy")

    def test_both_reject_a_retry_edge_without_a_reroute_edge(self):
        spec = {"schema": g.SCHEMA, "nodes": [
            {"name": "build", "agent": "coder"},
            {"name": "gate", "kind": "gate", "agent": "reviewer", "on": {"RETRY": "build"}},
        ]}
        self.assert_agrees(spec, "retry-without-reroute")
        self.assertTrue(g.validate_spec(spec), "this spec must be invalid")

    def test_both_reject_a_dependency_cycle(self):
        spec = {"schema": g.SCHEMA, "nodes": [
            {"name": "a", "agent": "x", "depends_on": ["b"]},
            {"name": "b", "agent": "x", "depends_on": ["a"]},
        ]}
        self.assert_agrees(spec, "cycle")
        self.assertTrue(g.validate_spec(spec))

    def test_both_accept_gate_back_edges(self):
        spec = {"schema": g.SCHEMA, "nodes": [
            {"name": "build", "agent": "coder"},
            {"name": "gate", "kind": "gate", "agent": "reviewer", "depends_on": ["build"],
             "on": {"PASS": "build", "RETRY": "build", "REROUTE": "build"}},
        ]}
        self.assert_agrees(spec, "back-edge")
        self.assertEqual(g.validate_spec(spec), [])

    def test_both_accept_the_approval_kind(self):
        spec = {"schema": g.SCHEMA, "nodes": [
            {"name": "ok", "kind": "approval", "agent": "manager"}]}
        self.assert_agrees(spec, "approval")
        self.assertEqual(g.validate_spec(spec), [])

    def test_both_reject_an_unknown_kind(self):
        self.assert_agrees(
            {"schema": g.SCHEMA, "nodes": [{"name": "x", "kind": "wat", "agent": "a"}]},
            "unknown-kind")

    def test_both_reject_a_missing_agent(self):
        self.assert_agrees(
            {"schema": g.SCHEMA, "nodes": [{"name": "x", "kind": "work"}]}, "no-agent")

    def test_both_reject_an_empty_spec(self):
        self.assert_agrees({"schema": g.SCHEMA, "nodes": []}, "empty")

    def test_both_reject_a_bad_budget(self):
        self.assert_agrees(
            {"schema": g.SCHEMA, "nodes": [{"name": "a", "agent": "x"}],
             "budget": {"maxNodeRuns": 0}}, "bad-budget")


@unittest.skipIf(NODE is None, "node is not installed")
@unittest.skipUnless(CHECKER.exists(), f"ultron checkout not found at {ULTRON}")
class RoutingParity(unittest.TestCase):
    """The router is the safety mechanism, so it must be identical on both engines."""

    GATE = {
        "name": "review", "kind": "gate", "agent": "reviewer",
        "on": {"PASS": "ship", "RETRY": "fix", "REROUTE": "redesign", "ESCALATE": "deep"},
    }

    def decide_locally(self, case: dict) -> dict:
        code = (case["verdict"].get("reasons") or [{}])[0].get("code")
        verdict = g.Verdict.from_dict(case["verdict"])
        ledger = g.AttemptLedger()
        for _ in range(case.get("retries", 0)):
            ledger.record(self.GATE["name"], verdict)
        # Distinct-code rejections exercise the code-independent backstop.
        for index in range(case.get("rejections", 0)):
            ledger.record(self.GATE["name"],
                          g.Verdict(g.RETRY, (g.Reason(f"NOVEL_{index}"),)))
        for _ in range(case.get("reroutes", 0)):
            ledger.record_reroute(self.GATE["name"], code)
        for _ in range(case.get("escalations", 0)):
            ledger.record_escalation(self.GATE["name"], code)
        routing = g.route(verdict, case["node"], ledger,
                          no_progress=bool(case.get("noProgress")))
        return {"action": routing.action, "target": routing.target,
                "verdict": routing.verdict, "forced": routing.forced}

    def assert_same_decision(self, case: dict, label: str) -> None:
        mine = self.decide_locally(case)
        theirs = ultron("route", case)
        for key in ("action", "target", "verdict", "forced"):
            self.assertEqual(
                mine[key], theirs[key],
                f"{label}: '{key}' differs - alfred={mine} ultron={theirs}",
            )

    def cases(self):
        retry = {"verdict": "RETRY", "reasons": [{"code": "TESTS_FAILED"}]}
        return [
            ("pass", {"verdict": {"verdict": "PASS", "reasons": []}, "node": self.GATE}),
            ("first-retry", {"verdict": retry, "node": self.GATE, "retries": 0}),
            ("second-retry", {"verdict": retry, "node": self.GATE, "retries": 1}),
            ("anti-thrash", {"verdict": retry, "node": self.GATE, "retries": 2}),
            ("no-progress", {"verdict": retry, "node": self.GATE, "noProgress": True}),
            ("reroute-exhausted", {"verdict": retry, "node": self.GATE,
                                   "retries": 2, "reroutes": 2}),
            ("escalation-exhausted", {"verdict": retry, "node": self.GATE,
                                      "retries": 2, "reroutes": 2, "escalations": 1}),
            ("gate-reroute", {"verdict": {"verdict": "REROUTE", "reasons": [{"code": "WRONG"}]},
                              "node": self.GATE}),
            ("gate-escalate", {"verdict": {"verdict": "ESCALATE", "reasons": [{"code": "HARD"}]},
                               "node": self.GATE}),
            ("escalate-twice", {"verdict": {"verdict": "ESCALATE", "reasons": [{"code": "HARD"}]},
                                "node": self.GATE, "escalations": 1}),
            ("abort", {"verdict": {"verdict": "ABORT", "reasons": [{"code": "UNSAFE"}]},
                       "node": self.GATE}),
            # The code-independent backstop: novel codes must not buy more retries.
            ("rejection-cap", {"verdict": retry, "node": self.GATE, "rejections": 4}),
            ("under-rejection-cap", {"verdict": retry, "node": self.GATE, "rejections": 1}),
        ]

    def test_the_router_agrees_across_the_whole_ladder(self):
        for label, case in self.cases():
            with self.subTest(case=label):
                self.assert_same_decision(case, label)

    def test_the_rejection_cap_is_identical_on_both_engines(self):
        theirs = ultron("route", {"verdict": {"verdict": "PASS", "reasons": []},
                                  "node": self.GATE})["bounds"]
        self.assertEqual(theirs["rejections"], g.MAX_GATE_REJECTIONS)

    def test_missing_edges_fail_closed_on_both(self):
        bare = {"name": "review", "kind": "gate", "agent": "r", "on": {"PASS": "ship"}}
        for label, case in (
            ("retry-no-edge", {"verdict": {"verdict": "RETRY", "reasons": [{"code": "X"}]},
                               "node": bare}),
            ("escalate-no-edge", {"verdict": {"verdict": "ESCALATE", "reasons": [{"code": "X"}]},
                                  "node": bare}),
        ):
            with self.subTest(case=label):
                self.assert_same_decision(case, label)

    def test_the_ladder_bounds_are_numerically_identical(self):
        theirs = ultron("route", {"verdict": {"verdict": "PASS", "reasons": []},
                                  "node": self.GATE})["bounds"]
        self.assertEqual(theirs["retries"], g.MAX_SAME_REASON_RETRIES)
        self.assertEqual(theirs["reroutes"], g.MAX_SAME_REASON_REROUTES)
        self.assertEqual(theirs["escalations"], g.MAX_SAME_REASON_ESCALATIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
