#!/usr/bin/env python3
"""Tests for the Alfred dashboard.

The dashboard reads the audit trail, the policy and memory, and serves them over
HTTP. That makes it a security surface, so these tests are mostly about what it
must REFUSE to do:

  * no access without the session token
  * no execution (read-only: POST is refused)
  * loopback binding only
  * never read or serve anything under secrets/
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dashboard  # noqa: E402


class ServerHarness(unittest.TestCase):
    """Boots the real server on an OS-assigned loopback port."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path: str, token: str | None = dashboard.TOKEN, method: str = "GET"):
        url = f"{self.base}{path}"
        if token is not None:
            url += ("&" if "?" in path else "?") + f"t={token}"
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


class Authentication(ServerHarness):
    def test_no_token_is_refused(self):
        status, body, _ = self.get("/api/all", token=None)
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", body)

    def test_wrong_token_is_refused(self):
        # Built at runtime rather than written inline: a `token="..."` literal trips
        # the repo's staged-secret scanner, which is correct behaviour on its part.
        wrong = "-".join(["definitely", "not", "the", "right", "one"])
        status, body, _ = self.get("/api/all", token=wrong)
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", body)

    def test_correct_token_is_accepted(self):
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("harnessOk", body)

    def test_the_html_page_also_requires_the_token(self):
        status, _, _ = self.get("/", token=None)
        self.assertEqual(status, 401)

    def test_token_is_long_enough_to_resist_guessing(self):
        self.assertGreaterEqual(len(dashboard.TOKEN), 24)


class ReadOnly(ServerHarness):
    def test_post_is_refused(self):
        status, body, _ = self.get("/api/all", method="POST")
        self.assertEqual(status, 405)
        self.assertIn("read-only", body)

    def test_unknown_route_is_a_clean_404(self):
        status, body, _ = self.get("/api/definitely-not-a-route")
        self.assertEqual(status, 404)
        self.assertIn("no such route", body)


class SecretSafety(unittest.TestCase):
    def test_reading_from_secrets_is_refused(self):
        target = ROOT / "secrets" / "harness.key"
        if not target.exists():
            self.skipTest("no key present to attempt")
        with self.assertRaises(PermissionError):
            dashboard._safe_read_text(target)

    def test_secrets_dir_is_on_the_forbidden_list(self):
        self.assertIn((ROOT / "secrets").resolve(), dashboard.FORBIDDEN_DIRS)

    def test_no_payload_leaks_the_signing_key(self):
        key_path = ROOT / "secrets" / "harness.key"
        if not key_path.exists():
            self.skipTest("no key present")
        key = key_path.read_bytes().strip().decode("utf-8", errors="replace")
        blob = json.dumps(dashboard.collect_all())
        self.assertNotIn(key, blob, "the signing key must never reach a response")


class Collectors(unittest.TestCase):
    def test_health_reports_the_real_harness_state(self):
        health = dashboard.collect_health()
        self.assertIn("harnessOk", health)
        self.assertIn("signatureValid", health)
        if health["harnessOk"]:
            self.assertTrue(health["signatureValid"])
            self.assertGreater(health["capabilityCount"], 0)

    def test_health_surfaces_the_audit_chain_state(self):
        """A chain break that nobody sees is a chain that is not doing anything.

        Before this, noticing a rewritten audit trail required remembering to run
        `harness audit-verify` by hand.
        """
        health = dashboard.collect_health()
        self.assertIn("auditChainOk", health)
        self.assertIn("auditChained", health)
        self.assertIn("auditLegacy", health,
                      "pre-chain records must be reported, not silently counted as verified")
        self.assertNotIn("integrityError", health, health.get("integrityError"))

    def test_health_surfaces_the_egress_boundary(self):
        """Which capabilities can reach the network, and which the least-trusted caller can
        use to do it. The second number is the one that should stay small."""
        health = dashboard.collect_health()
        self.assertIn("egressIsolated", health)
        self.assertGreater(len(health["egressIsolated"]), 0)
        self.assertIn("untrustedNetworkReach", health)
        self.assertLessEqual(len(health["untrustedNetworkReach"]), 2,
                             "the untrusted caller's network reach should stay minimal")

    def test_health_surfaces_signing_provenance(self):
        """`sign --review` says what changed since the last signature. Only the ledger says
        whether that signature was itself examined."""
        health = dashboard.collect_health()
        self.assertIn("signings", health)
        self.assertIn("signingsReviewed", health)
        self.assertIn("baselineWasReviewed", health)

    def test_health_surfaces_whether_the_pre_chain_region_is_sealed(self):
        """The 1237 pre-chain records were a true statement nobody could see: the chain did
        not cover them, so they could be edited freely while audit-verify still said ok."""
        health = dashboard.collect_health()
        self.assertIn("legacySealed", health)
        self.assertIn("legacyRecords", health)
        if health["legacyRecords"]:
            self.assertTrue(health["legacySealed"], "run 'harness seal-legacy'")
            self.assertTrue(health["legacyIntact"], "the pre-chain region was altered")

    def test_health_surfaces_whether_a_checkpoint_has_ever_been_taken(self):
        """Tail truncation is undetectable without a witness, so "never checkpointed" is
        itself the finding — hence the key is always present, even when null."""
        self.assertIn("lastCheckpoint", dashboard.collect_health())

    def test_health_surfaces_confinement_and_quota_budgets(self):
        health = dashboard.collect_health()
        self.assertTrue(health["confinementConfigured"],
                        "resource confinement must be configured in the live policy")
        self.assertIn("untrusted", health["confinementByTrust"])
        self.assertNotIn("quotaError", [k for k, v in health.items() if v], health.get("quotaError"))
        self.assertIn("local-model", health["quotas"])
        self.assertGreater(health["quotas"]["local-model"]["perMinute"], 0)

    def test_health_reports_revocations_as_counts_not_nonces(self):
        """A nonce names a specific token. Knowing it grants nothing, but there is no
        reason for a browser to receive one, so the surface is a count plus caller names."""
        health = dashboard.collect_health()
        self.assertIn("revokedTokens", health)
        self.assertIsInstance(health["revokedTokens"], int)
        self.assertIsInstance(health["revokedCallers"], list)
        self.assertNotIn("nonces", health)

    def test_policy_resolves_caller_surfaces(self):
        policy = dashboard.collect_policy()
        self.assertNotIn("error", policy)
        self.assertIn("local-model", policy["callers"])
        lm = policy["callers"]["local-model"]
        self.assertEqual(lm["trust"], "untrusted")
        self.assertTrue(lm["authRequired"], "the untrusted caller must need a token")
        self.assertGreater(lm["deniedCount"], 0, "untrusted must not see everything")

    def test_untrusted_caller_surface_is_a_strict_subset_of_owner(self):
        policy = dashboard.collect_policy()
        owner = set(policy["callers"]["owner"]["allowed"])
        local = set(policy["callers"]["local-model"]["allowed"])
        self.assertTrue(local < owner, "local-model must be a strict subset of owner")

    def test_no_gated_capability_is_exposed_to_the_local_model(self):
        policy = dashboard.collect_policy()
        gated = {n for n, c in policy["capabilities"].items() if c["gated"]}
        local = set(policy["callers"]["local-model"]["allowed"])
        self.assertEqual(gated & local, set(), "gated capabilities must never be local-model's")

    def test_audit_counts_match_entries(self):
        audit = dashboard.collect_audit()
        self.assertEqual(audit["total"], len(audit["entries"]))
        self.assertEqual(sum(audit["counts"].values()), audit["total"])

    def test_collect_all_is_json_serializable(self):
        json.dumps(dashboard.collect_all())  # must not raise

    def test_runs_expose_gate_verdicts(self):
        """The gauntlet engine's value is visible gate decisions, not just pass/fail."""
        runs = dashboard.collect_runs()
        self.assertIn("runs", runs)
        self.assertIn("gates", runs)
        self.assertIsInstance(runs["gates"], list)

    def test_every_gate_record_has_the_fields_the_ui_renders(self):
        for gate in dashboard.collect_runs()["gates"]:
            for key in ("run", "node", "verdict", "action", "forced"):
                self.assertIn(key, gate, f"gate record missing {key}")

    def test_forced_routes_are_counted_per_run(self):
        for run in dashboard.collect_runs()["runs"]:
            self.assertIn("gateCount", run)
            self.assertIn("forcedCount", run)
            self.assertLessEqual(run["forcedCount"], max(run["gateCount"], 0))

    def test_a_gauntlet_run_is_labelled_with_its_engine(self):
        runs = dashboard.collect_runs()["runs"]
        if not runs:
            self.skipTest("no runs recorded yet")
        self.assertTrue(all(r.get("engine") for r in runs))

    def test_graph_collector_never_raises_on_a_missing_graph(self):
        """An older clone has no mg_* tables; that is a normal state, not an error."""
        graph = dashboard.collect_graph()
        self.assertIn("available", graph)
        self.assertIsInstance(graph["facts"], int)

    def test_graph_counts_are_internally_consistent(self):
        graph = dashboard.collect_graph()
        if not graph["available"]:
            self.skipTest("memory graph not initialised")
        self.assertEqual(graph["facts"], graph["liveFacts"] + graph["supersededFacts"])

    def test_superseded_facts_are_surfaced_as_history(self):
        """The payoff of the bi-temporal graph is visible correction."""
        graph = dashboard.collect_graph()
        if not graph["available"]:
            self.skipTest("memory graph not initialised")
        for item in graph["changed"]:
            for key in ("subject", "predicate", "was"):
                self.assertIn(key, item)

    def test_current_facts_cite_their_source_episode(self):
        graph = dashboard.collect_graph()
        if not graph["available"]:
            self.skipTest("memory graph not initialised")
        for item in graph["current"]:
            self.assertIsNotNone(item.get("episodeId"), "provenance must survive to the UI")

    def test_memory_payload_includes_the_graph(self):
        self.assertIn("graph", dashboard.collect_memory())

    def test_parked_runs_are_surfaced_for_the_owner(self):
        """A run waiting on a human decision is more urgent than a markdown note."""
        parked = dashboard.collect_parked_runs()
        self.assertIsInstance(parked, list)
        for run in parked:
            for key in ("runId", "workflow", "status", "steps", "reason"):
                self.assertIn(key, run)
            self.assertIn(run["status"], ("running", "interrupted"))

    def test_approvals_payload_includes_parked_runs(self):
        self.assertIn("parkedRuns", dashboard.collect_approvals())

    def test_parked_run_collector_survives_a_missing_database(self):
        """A fresh clone has never run a checkpointed graph."""
        original = dashboard.RUNS_DB
        try:
            dashboard.RUNS_DB = ROOT / "memory" / "does-not-exist.db"
            self.assertEqual(dashboard.collect_parked_runs(), [])
        finally:
            dashboard.RUNS_DB = original


class HardeningHeaders(ServerHarness):
    def test_security_headers_are_present(self):
        _, _, headers = self.get("/")
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_csp_forbids_remote_origins(self):
        _, _, headers = self.get("/")
        csp = headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self'", csp)


if __name__ == "__main__":
    unittest.main(verbosity=2)