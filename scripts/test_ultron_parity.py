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

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_COUNTER = itertools.count()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gauntlet as g  # noqa: E402
import harness_guards as guards  # noqa: E402

ULTRON = Path("C:/projects/ultron-cli")
CHECKER = ULTRON / "scripts" / "gauntlet-check.mjs"
GUARDS_MJS = ULTRON / "src" / "guards.mjs"
NODE = shutil.which("node")


def node_eval(script: str) -> str:
    """Run a snippet of Node against Ultron's guards module and return its stdout.

    The temp file is written INSIDE the Ultron checkout, not into the system temp
    directory: an ES module's relative import resolves against the importing file's own
    location, so a snippet in %TEMP% looks for %TEMP%/src/guards.mjs and finds nothing.
    """
    temp = ULTRON / f".parity-{os.getpid()}-{next(_COUNTER)}.mjs"
    temp.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(
            [NODE, str(temp)], cwd=str(ULTRON), capture_output=True, text=True,
            # Explicit UTF-8. With text=True Python decodes using the locale codepage,
            # which on this machine is cp1252 - so Node's UTF-8 "café" came back as
            # "cafÃ©" and a byte-for-byte parity assertion failed on the DECODING rather
            # than on any real disagreement between the engines.
            encoding="utf-8", errors="replace",
            timeout=60, shell=False,
        )
        if proc.returncode != 0:
            raise AssertionError(f"node snippet failed: {proc.stderr.strip()[:400]}")
        return proc.stdout.strip()
    finally:
        temp.unlink(missing_ok=True)


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


@unittest.skipUnless(NODE and GUARDS_MJS.exists(), "Node or Ultron's guards module is absent")
class GuardParity(unittest.TestCase):
    """The guards must hold identically in both engines, not just the router.

    Alfred and Ultron share the gauntlet/v1 spec, and the project's rule is that a
    guarantee belongs to the SPEC rather than to whichever runtime executes it. An
    output cap that Alfred enforces and Ultron does not is a cap you escape by typing a
    different binary; an audit chain only one engine can verify is an audit trail the
    other engine has to be trusted about.

    So these tests do not compare implementations - they compare OUTPUTS across the
    language boundary, byte for byte.
    """

    def test_canonical_json_is_identical_across_engines(self):
        """The foundation. If the two disagree on serialization, every hash diverges."""
        value = {"b": 1, "a": [2, {"d": 4, "c": 3}], "z": "caf\u00e9", "t": True, "n": None}
        mine = guards.canonical_json(value)
        theirs = node_eval(
            "import { canonicalJson } from './src/guards.mjs';\n"
            f"process.stdout.write(canonicalJson({json.dumps(value)}));\n"
        )
        self.assertEqual(mine, theirs)

    def test_chain_hash_is_identical_across_engines(self):
        prev = "a" * 64
        payload = '{"capability":"status","seq":1}'
        mine = guards.chain_hash(prev, payload)
        theirs = node_eval(
            "import { chainHash } from './src/guards.mjs';\n"
            f"process.stdout.write(chainHash({json.dumps(prev)}, {json.dumps(payload)}));\n"
        )
        self.assertEqual(mine, theirs)

    def test_alfred_verifies_a_chain_that_ultron_wrote(self):
        """The real claim: neither engine has to be trusted about its own history."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "audit.jsonl"
            node_eval(
                "import { appendAudit } from './src/guards.mjs';\n"
                f"const f = {json.dumps(str(log))};\n"
                "for (let i = 0; i < 4; i += 1) appendAudit(f, { event: 'run', stage: 's' + i });\n"
                "process.stdout.write('done');\n"
            )
            state = guards.chain_verify(log)
            self.assertTrue(state["ok"], state)
            self.assertEqual(state["chained"], 4)
            self.assertEqual(state["legacy"], 0, "Ultron's records must be covered, not skipped")

    def test_ultron_verifies_a_chain_that_alfred_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "audit.jsonl"
            for i in range(4):
                guards.chain_append(log, {"event": "run", "stage": f"s{i}"})
            out = node_eval(
                "import { verifyAudit } from './src/guards.mjs';\n"
                f"process.stdout.write(JSON.stringify(verifyAudit({json.dumps(str(log))})));\n"
            )
            state = json.loads(out)
            self.assertTrue(state["ok"], state)
            self.assertEqual(state["chained"], 4)
            self.assertEqual(state["legacy"], 0)

    def test_tampering_is_detected_by_the_other_engine(self):
        """A break introduced after Ultron wrote the log is caught by Alfred, at the
        same line number Ultron would report."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "audit.jsonl"
            node_eval(
                "import { appendAudit } from './src/guards.mjs';\n"
                f"const f = {json.dumps(str(log))};\n"
                "for (let i = 0; i < 4; i += 1) appendAudit(f, { event: 'run', stage: 's' + i });\n"
                "process.stdout.write('done');\n"
            )
            lines = log.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[1])
            record["stage"] = "rewritten"
            lines[1] = json.dumps(record)
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")

            mine = guards.chain_verify(log)
            self.assertFalse(mine["ok"])
            self.assertEqual(mine["brokenAt"], 2)

            theirs = json.loads(node_eval(
                "import { verifyAudit } from './src/guards.mjs';\n"
                f"process.stdout.write(JSON.stringify(verifyAudit({json.dumps(str(log))})));\n"
            ))
            self.assertFalse(theirs["ok"])
            self.assertEqual(theirs["brokenAt"], mine["brokenAt"],
                             "both engines must name the same line as the break")

    def test_scoped_tokens_are_interchangeable_across_engines(self):
        """Alfred can mint a token Ultron accepts, so the two can hand credentials to
        each other without a shared secret format drifting apart."""
        token = guards.mint_token(b"shared-parity-key", "agent", ["read"], 300, now=1000.0)
        out = node_eval(
            "import { verifyToken } from './src/guards.mjs';\n"
            f"process.stdout.write(JSON.stringify(verifyToken('shared-parity-key', {json.dumps(token)}, "
            "'agent', 'read', 1000)));\n"
        )
        claims = json.loads(out)
        self.assertEqual(claims["scopes"], ["read"])
        self.assertEqual(claims["subject"], "agent")

    def test_a_token_ultron_minted_verifies_in_alfred(self):
        token = node_eval(
            "import { mintToken } from './src/guards.mjs';\n"
            "process.stdout.write(mintToken('shared-parity-key', 'agent', ['read'], 300, 1000));\n"
        )
        claims = guards.verify_token(b"shared-parity-key", token, "agent", "read", now=1000.0)
        self.assertEqual(claims["scopes"], ["read"])
        self.assertEqual(claims["iat"], 1000, "both engines must place iat identically")

    def test_both_engines_honour_the_same_revocation_state(self):
        """A revocation recorded by one engine must bind the other.

        Otherwise "revoke this credential" means "revoke it for whichever runtime happens
        to check", which is not revocation at all.
        """
        token = guards.mint_token(b"shared-parity-key", "agent", ["read"], 600, now=1000.0)
        claims = guards.token_claims(b"shared-parity-key", token)
        revocations = {"nonces": {claims["nonce"]: claims["exp"]}, "callerEpochs": {}}

        with self.assertRaises(guards.GuardError):
            guards.verify_token(b"shared-parity-key", token, "agent", "read", now=1010.0,
                                revocations=revocations)

        verdict = node_eval(
            "import { verifyToken } from './src/guards.mjs';\n"
            f"try {{ verifyToken('shared-parity-key', {json.dumps(token)}, 'agent', 'read', 1010, "
            f"{json.dumps(revocations)}); process.stdout.write('ALLOWED'); }}\n"
            "catch (e) { process.stdout.write('REFUSED: ' + e.message); }\n"
        )
        self.assertTrue(verdict.startswith("REFUSED"), verdict)
        self.assertIn("revoked", verdict)

    def test_both_engines_agree_on_the_caller_epoch_boundary(self):
        """The boundary is where an off-by-one hides, so it is asserted on both sides."""
        token = guards.mint_token(b"shared-parity-key", "agent", ["read"], 600, now=1000.0)
        # epoch exactly equal to iat must NOT revoke (the +1 is applied when recording,
        # not when checking) — and epoch above iat must.
        for epoch, expect_allowed in ((1000, True), (1001, False)):
            with self.subTest(epoch=epoch):
                revocations = {"nonces": {}, "callerEpochs": {"agent": epoch}}
                if expect_allowed:
                    guards.verify_token(b"shared-parity-key", token, "agent", "read",
                                        now=1010.0, revocations=revocations)
                else:
                    with self.assertRaises(guards.GuardError):
                        guards.verify_token(b"shared-parity-key", token, "agent", "read",
                                            now=1010.0, revocations=revocations)
                verdict = node_eval(
                    "import { verifyToken } from './src/guards.mjs';\n"
                    f"try {{ verifyToken('shared-parity-key', {json.dumps(token)}, 'agent', 'read', 1010, "
                    f"{json.dumps(revocations)}); process.stdout.write('ALLOWED'); }}\n"
                    "catch (e) { process.stdout.write('REFUSED'); }\n"
                )
                self.assertEqual(verdict, "ALLOWED" if expect_allowed else "REFUSED",
                                 f"engines disagree at epoch={epoch}")

    def test_both_engines_refuse_the_same_windows_path_tricks(self):
        """One list of refusals, two implementations, same answers."""
        cases = [
            r"\\server\share\f",
            r"C:\p\notes.txt:payload",
            r"C:\p\CON",
            r"C:\p\nul.txt",
            r"C:\p\secrets.\key",
            r"C:\NOSUCH~1\x",
        ]
        for raw in cases:
            with self.subTest(path=raw):
                with self.assertRaises(guards.GuardError):
                    guards.safe_resolve(raw, ROOT)
                verdict = node_eval(
                    "import { safeResolve } from './src/guards.mjs';\n"
                    f"try {{ safeResolve({json.dumps(raw)}); process.stdout.write('ALLOWED'); }}\n"
                    "catch (e) { process.stdout.write('REFUSED'); }\n"
                )
                self.assertEqual(verdict, "REFUSED", f"Ultron must also refuse {raw}")

    def test_both_engines_expand_a_resolvable_short_name_rather_than_refusing_it(self):
        """A short name is only a problem if it SURVIVES resolution.

        Refusing `~N` up front rejected legitimate paths whose ancestor happens to be
        shortened — `C:\\Users\\RUNNER~1\\...` on a CI runner, or any username long enough for
        Windows to abbreviate. Alfred's CI caught that. Expanding first is also *stronger*:
        confinement then judges the real location instead of the abbreviation, so
        `C:\\PROGRA~1` is refused for being outside the workspace rather than for its spelling.
        """
        resolved = guards.safe_resolve(r"C:\PROGRA~1\does-not-exist-yet.txt", ROOT)
        self.assertIn("Program Files", str(resolved))
        self.assertFalse(guards.inside_roots(resolved, [str(ROOT)]))

        theirs = node_eval(
            "import { safeResolve, insideRoots } from './src/guards.mjs';\n"
            "const r = safeResolve('C:\\\\PROGRA~1\\\\does-not-exist-yet.txt');\n"
            f"process.stdout.write(JSON.stringify({{ resolved: r, inside: insideRoots(r, [{json.dumps(str(ROOT))}]) }}));\n"
        )
        payload = json.loads(theirs)
        self.assertIn("Program Files", payload["resolved"])
        self.assertFalse(payload["inside"])
        self.assertEqual(str(resolved).lower(), payload["resolved"].lower(),
                         "both engines must expand it to the same real path")

    def test_both_engines_agree_a_legitimate_path_is_allowed(self):
        """A guard that refuses everything is not parity, it is breakage."""
        target = str(ROOT / "scripts" / "harness.py")
        self.assertTrue(guards.safe_resolve(target, ROOT).exists())
        verdict = node_eval(
            "import { safeResolve } from './src/guards.mjs';\n"
            f"try {{ safeResolve({json.dumps(target)}); process.stdout.write('ALLOWED'); }}\n"
            "catch (e) { process.stdout.write('REFUSED: ' + e.message); }\n"
        )
        self.assertEqual(verdict, "ALLOWED")

    def test_output_caps_truncate_at_the_same_boundary(self):
        """Both engines keep exactly the prefix that fits and both admit to cutting."""
        import io

        payload = b"x" * 5000
        mine_text, mine_cut = guards.bounded_read(io.BytesIO(payload), 100)
        theirs = json.loads(node_eval(
            "import { boundedCapture } from './src/guards.mjs';\n"
            "const s = boundedCapture(100); s.push('x'.repeat(5000));\n"
            "process.stdout.write(JSON.stringify({ bytes: s.bytes, truncated: s.truncated }));\n"
        ))
        self.assertEqual(len(mine_text), 100)
        self.assertEqual(theirs["bytes"], 100)
        self.assertTrue(mine_cut)
        self.assertTrue(theirs["truncated"])

    def test_redaction_withholds_the_same_values_in_both_engines(self):
        fields = {"pipeline": "feature", "apiKey": "abcd", "brandNew": "x"}
        mine = guards.redact(fields, ["pipeline"])
        theirs = json.loads(node_eval(
            "import { redact } from './src/guards.mjs';\n"
            f"process.stdout.write(JSON.stringify(redact({json.dumps(fields)}, ['pipeline'])));\n"
        ))
        self.assertEqual(mine, theirs)

    def test_both_engines_use_the_same_egress_isolation_wrapper(self):
        """The mechanisms differ — Alfred calls unshare itself, Ultron wraps argv with it — but
        the *invocation* must match. A difference here would mean one engine isolating loopback
        and the other not, so 'network: false' would mean two things."""
        import harness_confine as confine

        theirs = json.loads(node_eval(
            "import { NETNS_PREFIX } from './src/guards.mjs';\n"
            "process.stdout.write(JSON.stringify(NETNS_PREFIX));\n"
        ))
        self.assertEqual(list(confine.NETNS_PREFIX), theirs,
                         "the two engines must enter the network namespace identically")

    def test_neither_engine_maps_the_child_to_root(self):
        """`--map-root-user` would make a child believe it is uid 0. Asserted on both sides
        because it is a one-word change that would go unnoticed."""
        import harness_confine as confine

        self.assertIn("--map-current-user", confine.NETNS_PREFIX)
        verdict = node_eval(
            "import { NETNS_PREFIX } from './src/guards.mjs';\n"
            "process.stdout.write(NETNS_PREFIX.includes('--map-root-user') ? 'ROOT' : 'CURRENT');\n"
        )
        self.assertEqual(verdict, "CURRENT")

    def test_ultron_confinement_is_honest_on_windows(self):
        """Alfred confines on Windows via Job Objects; Ultron cannot, and must not imply it
        does. This asserts the *admission*, which is the part that would rot silently."""
        note = node_eval(
            "import { confineArgv } from './src/guards.mjs';\n"
            "const r = confineArgv(['cmd'], { memoryBytes: 1024 }, { platform: 'win32' });\n"
            "process.stdout.write(JSON.stringify({ applied: r.applied, note: r.note }));\n"
        )
        payload = json.loads(note)
        self.assertEqual(payload["applied"], [])
        self.assertIn("no argv-level confinement", payload["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
