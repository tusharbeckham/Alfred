#!/usr/bin/env python3
"""Security tests for the Alfred harness. These are the tests that matter:
they assert an untrusted local model CANNOT use the harness to harm the machine.

Run: python scripts/test_harness.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "harness.py"
EXIT_OK, EXIT_POLICY, EXIT_DENIED, EXIT_INPUT, EXIT_FAILED = 0, 2, 3, 4, 5


def run(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=env,
    )


def mint_token(caller: str, scopes: list[str] | None = None, ttl: int = 120) -> str:
    """Mint a real scoped token through the CLI.

    Deliberately not constructed by hand: a test that builds its own token is testing
    the test's idea of the format, not the harness's. Going through `mint-token` means
    a change to the token format breaks these tests loudly instead of leaving them
    passing against a format nothing uses any more.

    ``ttl`` may be negative, which is how the expiry test gets an already-dead token
    without sleeping.
    """
    args = ["mint-token", caller, "--ttl", str(ttl)]
    for scope in scopes or []:
        args += ["--scope", scope]
    result = run(args)
    if result.returncode != 0:
        raise AssertionError(f"mint-token failed: {result.stderr}")
    return json.loads(result.stdout)["token"]


class PolicyIntegrity(unittest.TestCase):
    def test_policy_verifies(self):
        result = run(["verify"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["signatureValid"])
        self.assertTrue(payload["denyByDefault"])

    def test_tampering_with_the_policy_breaks_the_signature(self):
        """The core anti-local-model control: a model that edits the policy cannot use it."""
        policy_path = ROOT / "policy" / "harness-policy.json"
        original = policy_path.read_bytes()
        try:
            tampered = json.loads(original)
            # Simulate a hostile local model granting itself everything.
            tampered["callers"]["local-model"]["capabilities"] = ["*"]
            tampered["callers"]["local-model"]["authRequired"] = False
            policy_path.write_bytes(json.dumps(tampered, indent=2).encode("utf-8"))

            verify = run(["verify"])
            self.assertEqual(verify.returncode, EXIT_POLICY, "tampered policy must fail closed")
            self.assertIn("INTEGRITY FAILURE", verify.stderr)

            # And nothing can run at all while the policy is untrusted.
            attempt = run(["run", "status", "--caller", "local-model"])
            self.assertEqual(attempt.returncode, EXIT_POLICY)
        finally:
            policy_path.write_bytes(original)
        self.assertEqual(run(["verify"]).returncode, EXIT_OK, "policy restored")

    def test_signature_survives_crlf_line_endings(self):
        """Regression: a git checkout on Windows must not brick the harness.

        With core.autocrlf=true, git rewrites LF to CRLF on checkout. If the HMAC
        is taken over raw bytes, the signature stops verifying and the harness
        refuses to run ANYTHING - even though the policy content is authentic.
        This actually happened; the signature is now line-ending invariant.
        """
        policy_path = ROOT / "policy" / "harness-policy.json"
        original = policy_path.read_bytes()
        try:
            lf = original.replace(b"\r\n", b"\n")
            crlf = lf.replace(b"\n", b"\r\n")

            policy_path.write_bytes(lf)
            self.assertEqual(run(["verify"]).returncode, EXIT_OK,
                             "policy with LF endings must verify")

            policy_path.write_bytes(crlf)
            self.assertEqual(run(["verify"]).returncode, EXIT_OK,
                             "policy with CRLF endings must verify (git checkout on Windows)")
        finally:
            policy_path.write_bytes(original)

    def test_line_ending_normalization_does_not_mask_real_tampering(self):
        """The CRLF fix must not weaken tamper detection.

        A semantic change is still caught even when it arrives with the same
        line-ending style the signature was generated under.
        """
        policy_path = ROOT / "policy" / "harness-policy.json"
        original = policy_path.read_bytes()
        try:
            tampered = json.loads(original)
            tampered["settings"]["denyByDefault"] = False
            # Write with CRLF endings so only the *semantic* change can be detected.
            body = json.dumps(tampered, indent=2).encode("utf-8").replace(b"\n", b"\r\n")
            policy_path.write_bytes(body)

            verify = run(["verify"])
            self.assertEqual(verify.returncode, EXIT_POLICY,
                             "a semantic change must still fail closed under CRLF")
            self.assertIn("INTEGRITY FAILURE", verify.stderr)
        finally:
            policy_path.write_bytes(original)
        self.assertEqual(run(["verify"]).returncode, EXIT_OK, "policy restored")


class UntrustedLocalModelContainment(unittest.TestCase):
    """Every one of these must be refused."""

    def test_local_model_requires_a_token_it_does_not_have(self):
        result = run(["run", "status", "--caller", "local-model"])
        self.assertEqual(result.returncode, EXIT_DENIED)
        self.assertIn("requires a token", result.stderr)

    def test_local_model_cannot_run_gated_capabilities_even_with_approve(self):
        for capability, params in [
            ("workflow-run", ["--param", "workflow=feature", "--param", "task=x"]),
            ("backup", []),
            ("git-commit", ["--param", "message=x"]),
        ]:
            with self.subTest(capability=capability):
                result = run(["run", capability, "--caller", "local-model", "--approve", *params])
                self.assertEqual(result.returncode, EXIT_DENIED)

    def test_local_model_cannot_run_capabilities_outside_its_allowlist(self):
        """Even with a valid token, the allowlist still binds."""
        # local-model is `requireScopedToken`, so the credential is minted by the
        # harness rather than shared. Nothing credential-shaped is stored in the repo:
        # the token exists only for the life of this test and expires by itself.
        token = mint_token("local-model", ttl=120)
        # In its allowlist -> permitted.
        ok = run(["run", "disk-report", "--caller", "local-model", "--token", token])
        self.assertEqual(ok.returncode, EXIT_OK, ok.stderr)
        # NOT in its allowlist -> refused, despite a valid token.
        for capability, params in [
            ("ci", []),
            ("test", []),
            ("web-search", ["--param", "query=x"]),
            ("remember", ["--param", "type=fact", "--param", "topic=t", "--param", "text=x"]),
            ("workflow-run", ["--param", "workflow=feature", "--param", "task=x"]),
            ("backup", []),
        ]:
            with self.subTest(capability=capability):
                denied = run(["run", capability, "--caller", "local-model", "--token", token, "--approve", *params])
                self.assertEqual(denied.returncode, EXIT_DENIED, f"{capability} must be denied")

    def test_local_model_flat_shared_token_is_refused(self):
        """The untrusted caller must present a scoped token, not a shared secret.

        A flat token never expires and is bound to nothing, so one leak is permanent
        and usable against any capability the caller holds. For the caller we trust
        least, that is the wrong shape of credential - the policy now says so, and
        provisioning a flat one does not quietly re-enable it.
        """
        # Derived at runtime rather than written as a literal. The repo's own pre-commit
        # secret scanner flags credential-shaped string assignments, and it is right to:
        # the correct answer is not to silence it with --no-verify but to stop putting
        # things that look like credentials in source files.
        token = hashlib.sha256(b"harness-flat-token-fixture").hexdigest()[:24]
        store = ROOT / "secrets" / "harness-callers.json"
        existed = store.exists()
        backup = store.read_bytes() if existed else None
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(
                json.dumps({"local-model": hashlib.sha256(token.encode()).hexdigest()}),
                encoding="utf-8",
            )
            result = run(["run", "disk-report", "--caller", "local-model", "--token", token])
            self.assertEqual(result.returncode, EXIT_DENIED, result.stderr)
            self.assertIn("scoped token", result.stderr)
        finally:
            if existed:
                store.write_bytes(backup)
            else:
                store.unlink(missing_ok=True)

    def test_a_wrong_token_is_rejected(self):
        """A forged, garbled, or tampered token is refused - and so is a valid one
        whose MAC has been touched by a single character."""
        good = mint_token("local-model", ttl=120)
        # Flip one character of the MAC. Everything else about the token is authentic.
        tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
        for label, token in [
            ("garbage", "wrong"),
            ("right shape, wrong mac", "aht1.local-model.99999999999.abc.status.deadbeef" + "0" * 24),
            ("one flipped mac character", tampered),
        ]:
            with self.subTest(token=label):
                result = run(["run", "disk-report", "--caller", "local-model", "--token", token])
                self.assertEqual(result.returncode, EXIT_DENIED, result.stderr)


class ScopedTokens(unittest.TestCase):
    """Tokens carry their caller, expiry and capability list inside the HMAC.

    The threat is the confused deputy: the untrusted local model legitimately holds a
    token, and a prompt injection only needs to talk it into spending that token on a
    capability the attacker picked. Binding the capability into the signature means a
    token minted for reading cannot be aimed at writing.
    """

    def test_a_token_scoped_to_one_capability_does_not_work_on_another(self):
        token = mint_token("local-model", scopes=["graph-recall"], ttl=120)
        ok = run(["run", "graph-recall", "--caller", "local-model", "--token", token,
                  "--param", "query=test"])
        self.assertEqual(ok.returncode, EXIT_OK, ok.stderr)

        # Same caller, same allowlist, same valid unexpired token - different capability.
        denied = run(["run", "disk-report", "--caller", "local-model", "--token", token])
        self.assertEqual(denied.returncode, EXIT_DENIED, denied.stdout)
        self.assertIn("scoped", denied.stderr)

    def test_an_expired_token_is_refused(self):
        expired = mint_token("local-model", scopes=["disk-report"], ttl=-5)
        result = run(["run", "disk-report", "--caller", "local-model", "--token", expired])
        self.assertEqual(result.returncode, EXIT_DENIED, result.stdout)
        self.assertIn("expired", result.stderr)

    def test_a_token_cannot_be_replayed_as_another_caller(self):
        token = mint_token("local-model", scopes=["disk-report"], ttl=120)
        result = run(["run", "disk-report", "--caller", "scheduled", "--token", token])
        self.assertEqual(result.returncode, EXIT_DENIED, result.stdout)
        self.assertIn("issued for caller", result.stderr)

    def test_minting_cannot_grant_more_than_the_policy_allows(self):
        """A token is a narrowing device, never a widening one."""
        result = run(["mint-token", "local-model", "--scope", "backup"])
        self.assertEqual(result.returncode, EXIT_INPUT, result.stdout)
        self.assertIn("not permitted", result.stderr)

    def test_the_minted_token_is_not_written_to_the_audit_trail(self):
        """The trail records which token was used, by nonce, never the token itself."""
        token = mint_token("local-model", scopes=["disk-report"], ttl=120)
        run(["run", "disk-report", "--caller", "local-model", "--token", token])
        trail = (ROOT / "memory" / "harness-audit.jsonl").read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(token, trail, "a bearer token must never land in an append-only log")
        self.assertIn("tokenNonce", trail.splitlines()[-1])


class ReviewedSigning(unittest.TestCase):
    """Signing is the one step where a human blesses a change, and it was a rubber stamp.

    `harness sign` hashed whatever bytes were on disk and reported nothing about what they
    contained. A model that edits the policy cannot forge a signature — but it does not need
    to. It only needs the Owner to re-sign for some unrelated reason, at which point the edit
    is blessed for free. `sign --review` is what makes that not work.
    """

    def setUp(self):
        self.policy_path = ROOT / "policy" / "harness-policy.json"
        self.original = self.policy_path.read_bytes()

    def tearDown(self):
        self.policy_path.write_bytes(self.original)
        run(["sign"])
        self.assertEqual(run(["verify"]).returncode, EXIT_OK, "policy restored and re-signed")

    def _escalate(self):
        """The hostile edit: grant a write capability, drop the token, promote the caller."""
        policy = json.loads(self.original)
        policy["callers"]["local-model"]["capabilities"].append("remember")
        policy["callers"]["local-model"]["authRequired"] = False
        policy["callers"]["local-model"]["trust"] = "medium"
        self.policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

    def test_an_unchanged_policy_reports_identical(self):
        result = run(["diff"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertTrue(json.loads(result.stdout)["identical"])

    def test_diff_names_each_privilege_increase_and_why(self):
        self._escalate()
        delta = json.loads(run(["diff"]).stdout)
        reasons = {e["why"] for e in delta["privilegeIncreases"]}
        self.assertIn("a caller gained a capability", reasons)
        self.assertIn("a restriction was switched off", reasons)
        self.assertIn("a caller was promoted", reasons)

    def test_sign_review_refuses_a_privilege_increase(self):
        self._escalate()
        result = run(["sign", "--review"])
        self.assertEqual(result.returncode, EXIT_DENIED, result.stdout)
        self.assertIn("REFUSING TO SIGN", result.stderr)

    def test_sign_review_proceeds_when_the_escalation_is_accepted(self):
        """The gate must be passable, or it becomes something people route around."""
        self._escalate()
        result = run(["sign", "--review", "--accept-escalation"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertTrue(json.loads(result.stdout)["signed"])

    def test_sign_review_refuses_a_policy_with_lint_errors(self):
        """Two independent objections. Lint catches a policy that cannot mean what it says;
        the diff catches one that means something new. Neither subsumes the other."""
        policy = json.loads(self.original)
        policy["callers"]["kiro-agent"]["capabilities"].append("no-such-capability")
        self.policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        result = run(["sign", "--review"])
        self.assertEqual(result.returncode, EXIT_POLICY, result.stdout)
        self.assertIn("lint errors", result.stderr)

    def test_a_tightening_change_is_not_reported_as_an_escalation(self):
        """A review tool that cries wolf gets skimmed. Removing a grant is not a privilege
        increase and must not be flagged as one."""
        policy = json.loads(self.original)
        policy["callers"]["kiro-agent"]["capabilities"] = [
            c for c in policy["callers"]["kiro-agent"]["capabilities"] if c != "test"
        ]
        self.policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        delta = json.loads(run(["diff"]).stdout)
        self.assertEqual(delta["privilegeIncreases"], [], delta)
        self.assertTrue(any(".grants.test" in r["key"] for r in delta["removed"]))

    def test_reformatting_the_policy_is_not_a_change(self):
        """The diff is semantic. A textual diff would flag reindentation and key reordering
        while a caller quietly gaining a capability sat in the noise."""
        policy = json.loads(self.original)
        # Different indentation, reordered keys, reordered allowlist - same meaning.
        policy["callers"]["kiro-agent"]["capabilities"].reverse()
        reordered = {k: policy[k] for k in reversed(list(policy))}
        self.policy_path.write_text(json.dumps(reordered, indent=8), encoding="utf-8")
        delta = json.loads(run(["diff"]).stdout)
        self.assertTrue(delta["identical"],
                        f"reformatting must not register as a change: {delta}")

    def test_the_snapshot_is_refused_if_it_does_not_match_the_signature(self):
        """The snapshot is self-verifying, so editing it cannot fool the diff into
        reporting 'nothing changed'."""
        snapshot = ROOT / "policy" / "harness-policy.signed.json"
        saved = snapshot.read_bytes()
        try:
            tampered = json.loads(saved)
            tampered["callers"]["local-model"]["capabilities"].append("backup")
            snapshot.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
            payload = json.loads(run(["diff"]).stdout)
            self.assertEqual(payload.get("diff"), "unavailable",
                             "an unverifiable snapshot must be refused, not believed")
        finally:
            snapshot.write_bytes(saved)


class DenyByDefault(unittest.TestCase):
    def test_undefined_capability_is_refused(self):
        result = run(["run", "rm-rf-everything", "--caller", "owner"])
        self.assertEqual(result.returncode, EXIT_DENIED)
        self.assertIn("deny by default", result.stderr)

    def test_unknown_caller_is_refused(self):
        result = run(["run", "status", "--caller", "attacker"])
        self.assertEqual(result.returncode, EXIT_DENIED)
        self.assertIn("Unknown caller", result.stderr)

    def test_owner_must_approve_gated_capabilities(self):
        result = run(["run", "backup", "--caller", "owner"])
        self.assertEqual(result.returncode, EXIT_DENIED)
        self.assertIn("gated", result.stderr)

    def test_low_trust_scheduled_caller_cannot_run_gated_capabilities(self):
        result = run(["run", "workflow-run", "--caller", "scheduled", "--approve",
                      "--param", "workflow=feature", "--param", "task=x"])
        self.assertEqual(result.returncode, EXIT_DENIED)


class ParameterValidation(unittest.TestCase):
    def test_enum_rejects_anything_not_listed(self):
        for bogus in ["../../etc/passwd", "feature; rm -rf /", "FEATURE", ""]:
            with self.subTest(value=bogus):
                result = run(["run", "workflow-plan", "--caller", "owner", "--param", f"workflow={bogus}"])
                self.assertEqual(result.returncode, EXIT_INPUT)

    def test_unknown_parameter_is_rejected(self):
        result = run(["run", "status", "--caller", "owner", "--param", "evil=1"])
        self.assertEqual(result.returncode, EXIT_INPUT)

    def test_missing_parameter_is_rejected(self):
        result = run(["run", "workflow-plan", "--caller", "owner"])
        self.assertEqual(result.returncode, EXIT_INPUT)

    def test_path_cannot_escape_the_workspace(self):
        for bad in ["C:\\Windows\\System32", "C:\\Program Files", "C:\\Users\\Default",
                    "C:\\Alfred\\..\\Windows", "C:\\"]:
            with self.subTest(path=bad):
                result = run(["run", "git-status", "--caller", "owner", "--param", f"path={bad}"])
                self.assertEqual(result.returncode, EXIT_DENIED, f"{bad} must be denied")

    def test_path_cannot_reach_secrets_or_policy(self):
        for bad in ["C:\\Alfred\\secrets", "C:\\Alfred\\secrets\\harness.key",
                    "C:\\Alfred\\policy", "C:\\Alfred\\policy\\harness-policy.json"]:
            with self.subTest(path=bad):
                result = run(["run", "git-status", "--caller", "owner", "--param", f"path={bad}"])
                self.assertEqual(result.returncode, EXIT_DENIED, f"{bad} must be denied")

    def test_control_characters_are_rejected(self):
        result = run(["run", "recall", "--caller", "owner", "--param", "query=a\nb"])
        self.assertEqual(result.returncode, EXIT_INPUT)

    def test_overlong_string_is_rejected(self):
        result = run(["run", "recall", "--caller", "owner", "--param", f"query={'x' * 5000}"])
        self.assertEqual(result.returncode, EXIT_INPUT)


class NoShellInjection(unittest.TestCase):
    """The harness builds argv arrays, so shell metacharacters are inert data."""

    def test_injection_attempt_in_a_string_param_does_not_execute(self):
        canary = ROOT / "tmp" / "harness-injection-canary.txt"
        canary.unlink(missing_ok=True)
        payload = f'x & echo pwned > "{canary}"'
        result = run(["run", "recall", "--caller", "owner", "--param", f"query={payload}"])
        # It may succeed or fail depending on the memory index, but it must NEVER
        # have executed the injected command.
        self.assertFalse(canary.exists(), "shell injection must not execute")
        self.assertIn(result.returncode, (EXIT_OK, EXIT_FAILED, EXIT_INPUT))

    def test_forbidden_patterns_are_scanned_in_the_final_argv(self):
        result = run(["run", "remember", "--caller", "owner", "--param", "type=fact",
                      "--param", "topic=t", "--param", "text=please run Invoke-Expression now"])
        self.assertEqual(result.returncode, EXIT_DENIED)
        self.assertIn("forbidden pattern", result.stderr)


class ListingAndAudit(unittest.TestCase):
    def test_list_shows_a_narrower_surface_for_untrusted_callers(self):
        owner = json.loads(run(["list", "--caller", "owner"]).stdout)
        local = json.loads(run(["list", "--caller", "local-model"]).stdout)
        self.assertGreater(len(owner["allowed"]), len(local["allowed"]))
        self.assertEqual(local["trust"], "untrusted")
        self.assertTrue(local["authRequired"])
        for gated in ("workflow-run", "backup", "git-commit"):
            self.assertNotIn(gated, local["allowed"])
        self.assertFalse(any(spec["gated"] for spec in local["allowed"].values()),
                         "an untrusted caller must have zero gated capabilities")

    def test_every_local_model_capability_is_read_only(self):
        local = json.loads(run(["list", "--caller", "local-model"]).stdout)
        for name, spec in local["allowed"].items():
            self.assertEqual(spec["risk"], "read", f"{name} must be read-only for an untrusted caller")

    def test_denials_are_audited(self):
        log = ROOT / "memory" / "harness-audit.jsonl"
        before = log.stat().st_size if log.exists() else 0
        run(["run", "rm-rf-everything", "--caller", "owner"])
        self.assertTrue(log.exists(), "audit log must be created")
        self.assertGreater(log.stat().st_size, before, "the denial must be appended to the audit log")
        last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(last["decision"], "denied")

    def test_dry_run_does_not_execute(self):
        result = run(["run", "disk-report", "--caller", "owner", "--dry-run", "--json"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(json.loads(payload["stdout"])["dryRun"])


class HappyPath(unittest.TestCase):
    def test_owner_can_run_a_read_capability(self):
        result = run(["run", "disk-report", "--caller", "owner"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("FreeGB", result.stdout)

    def test_kiro_agent_can_run_a_workflow_plan(self):
        result = run(["run", "workflow-plan", "--caller", "kiro-agent", "--param", "workflow=feature"])
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
