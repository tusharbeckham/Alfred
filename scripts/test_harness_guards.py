#!/usr/bin/env python3
"""Tests for the harness guards — the controls underneath the capability policy.

These are unit tests on purpose. ``test_harness.py`` drives the real CLI and proves
the policy chain refuses what it should; this file proves the individual guards are
correct on inputs that are awkward to reach through the CLI — a junction pointing out
of the workspace, an audit trail someone edited, a bucket that has run dry.

Run: python scripts/test_harness_guards.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_confine as confine  # noqa: E402
import harness_guards as guards  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


class WindowsPathConfinement(unittest.TestCase):
    """The path guard exists because a path STRING and the file it opens are not the
    same thing on Windows. Every case here is a way to make them differ."""

    def test_unc_and_extended_length_paths_are_refused(self):
        for raw in [
            r"\\server\share\file.txt",
            r"\\?\C:\Alfred\secrets\harness.key",
            r"\\localhost\C$\Windows\System32\config\SAM",
            "//server/share/x",
        ]:
            with self.subTest(path=raw):
                with self.assertRaises(guards.GuardError) as ctx:
                    guards.safe_resolve(raw, ROOT)
                self.assertIn("UNC", str(ctx.exception))

    def test_alternate_data_streams_are_refused(self):
        """The visible path passes every check; the write lands in a hidden stream.

        This is the CVE-2025-8088 (WinRAR) shape: confinement approves 'notes.txt'
        while the bytes go to 'notes.txt:payload', which nothing later inspects.
        """
        for raw in [r"C:\Alfred\notes.txt:payload", "notes.txt:hidden:$DATA", r"C:\Alfred\a:b"]:
            with self.subTest(path=raw):
                with self.assertRaises(guards.GuardError) as ctx:
                    guards.safe_resolve(raw, ROOT)
                self.assertIn("alternate data stream", str(ctx.exception))

    def test_dos_device_names_are_refused_in_any_component(self):
        """CON/NUL/COM1 are magic in every directory and never touch the filesystem.

        A capability pointed at one either blocks forever or discards its output, so
        the honest answer is to refuse rather than to hang the harness.
        """
        for raw in [
            r"C:\Alfred\CON",
            r"C:\Alfred\NUL",
            r"C:\Alfred\nul.txt",     # trailing extension does not save you
            r"C:\Alfred\COM1",
            r"C:\Alfred\LPT9\file",   # mid-path, not just the leaf
            r"C:\Alfred\aux",
        ]:
            with self.subTest(path=raw):
                with self.assertRaises(guards.GuardError) as ctx:
                    guards.safe_resolve(raw, ROOT)
                self.assertIn("device", str(ctx.exception))

    def test_trailing_dots_and_spaces_are_refused(self):
        """Windows silently strips these, so the path checked is not the path used."""
        for raw in [r"C:\Alfred\secrets.\key", r"C:\Alfred\evil \file"]:
            with self.subTest(path=raw):
                with self.assertRaises(guards.GuardError):
                    guards.safe_resolve(raw, ROOT)

    def test_short_names_are_refused_when_unresolvable(self):
        with self.assertRaises(guards.GuardError) as ctx:
            guards.safe_resolve(r"C:\NOSUCH~1\file.txt", ROOT)
        self.assertIn("short name", str(ctx.exception))

    @unittest.skipUnless(WINDOWS, "8.3 short names are a Windows/NTFS feature")
    def test_an_existing_short_named_ancestor_does_not_refuse_a_new_file(self):
        """Regression, found by CI and invisible on my machine.

        ``GetLongPathNameW`` only expands a path that EXISTS. So a capability writing a file
        that is not there yet, under a directory whose own name Windows has shortened, failed
        expansion and was then refused for containing ``~N`` — even though the only short
        component was a legitimate part of the user's home path. GitHub's Windows runners hit
        it (``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\...``) and so would any user whose
        username is long enough to be shortened.

        ``C:\\PROGRA~1`` is a real alias on every Windows install, so this reproduces the shape
        deterministically: existing short-named ancestor, non-existent tail.
        """
        expanded = guards._expand_short_name(r"C:\PROGRA~1\does-not-exist-yet.txt")
        self.assertNotIn("~", expanded, "the existing ancestor must be expanded")
        self.assertTrue(expanded.endswith("does-not-exist-yet.txt"), expanded)

        # It now resolves rather than being refused for the wrong reason — and resolving is
        # what lets confinement judge the REAL location instead of the abbreviation.
        resolved = guards.safe_resolve(r"C:\PROGRA~1\does-not-exist-yet.txt", ROOT)
        self.assertIn("Program Files", str(resolved))
        self.assertFalse(guards.inside_roots(resolved, [str(ROOT)]),
                         "and confinement still refuses it, for the right reason")

    def test_a_new_file_under_a_normal_ancestor_resolves(self):
        """The everyday write case: the target does not exist yet and that is fine."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = guards.safe_resolve(str(base / "sub" / "new.txt"), base)
            self.assertTrue(str(target).endswith("new.txt"))
            self.assertTrue(guards.inside_roots(target, [str(base)]))

    def test_nul_byte_is_refused(self):
        with self.assertRaises(guards.GuardError):
            guards.safe_resolve("C:/Alfred/ok\x00/../../evil", ROOT)

    def test_traversal_still_resolves_out_and_is_caught_by_confinement(self):
        escaped = guards.safe_resolve(r"C:\Alfred\..\Windows\System32", ROOT)
        self.assertFalse(guards.inside_roots(escaped, ["C:/Alfred"]))

    def test_a_legitimate_path_is_accepted_unchanged(self):
        resolved = guards.safe_resolve(str(ROOT / "scripts" / "harness.py"), ROOT)
        self.assertTrue(resolved.exists())
        self.assertTrue(guards.inside_roots(resolved, [str(ROOT)]))

    def test_relative_paths_resolve_against_the_base(self):
        resolved = guards.safe_resolve("scripts/harness.py", ROOT)
        self.assertEqual(resolved, Path(os.path.realpath(ROOT / "scripts" / "harness.py")))

    def test_unicode_variants_of_the_same_path_agree(self):
        """Two byte sequences that display identically must not give two answers."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            nfc = "caf\u00e9"          # é as one code point
            nfd = "cafe\u0301"         # e + combining acute
            a = guards.safe_resolve(str(base / nfc), base)
            b = guards.safe_resolve(str(base / nfd), base)
            self.assertEqual(str(a), str(b))

    @unittest.skipUnless(WINDOWS, "junctions are a Windows/NTFS feature")
    def test_a_junction_out_of_the_workspace_is_caught(self):
        """A junction is the cheapest escape: any user can make one, no admin needed.

        The old check used Path.resolve(), which historically did not follow junctions
        on Windows, so a link inside the workspace pointing outside it would pass
        confinement while opening the target. realpath() follows it, so the real
        destination is what gets confined.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            outside = Path(tmp) / "outside"
            workspace.mkdir()
            outside.mkdir()
            (outside / "loot.txt").write_text("secret", encoding="utf-8")
            link = workspace / "escape"
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                  capture_output=True, text=True)
            if made.returncode != 0:
                self.skipTest(f"could not create a junction: {made.stderr.strip()}")

            resolved = guards.safe_resolve(str(link / "loot.txt"), workspace)
            self.assertFalse(
                guards.inside_roots(resolved, [str(workspace)]),
                "a junction target outside the workspace must not be reported as inside",
            )

    def test_case_insensitivity_does_not_defeat_confinement(self):
        self.assertTrue(guards.inside_roots(Path(str(ROOT).upper()) / "scripts", [str(ROOT)])
                        if WINDOWS else True)


class BoundedOutput(unittest.TestCase):
    """Unbounded child output is a denial of service against the harness itself,
    reachable by any caller allowed to run any capability at all."""

    def test_output_is_capped_and_truncation_is_reported(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5_000_000)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        text, truncated = guards.bounded_read(proc.stdout, 1024)
        proc.stdout.close()
        proc.stderr.close()
        proc.wait(timeout=30)
        self.assertEqual(len(text), 1024)
        self.assertTrue(truncated, "truncation must be reported, not silently hidden")

    def test_short_output_is_not_marked_truncated(self):
        proc = subprocess.Popen([sys.executable, "-c", "print('hello')"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        text, truncated = guards.bounded_read(proc.stdout, 1024)
        proc.stdout.close()
        proc.stderr.close()
        proc.wait(timeout=30)
        self.assertIn("hello", text)
        self.assertFalse(truncated)


class AuditHashChain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "audit.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_clean_chain_verifies(self):
        for i in range(5):
            guards.chain_append(self.log, {"capability": f"cap{i}", "decision": "executed"})
        state = guards.chain_verify(self.log)
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["chained"], 5)

    def test_editing_a_record_breaks_the_chain_at_that_line(self):
        for i in range(5):
            guards.chain_append(self.log, {"capability": f"cap{i}"})
        lines = self.log.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[2])
        record["capability"] = "something-else"   # rewrite history
        lines[2] = json.dumps(record, ensure_ascii=False)
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        state = guards.chain_verify(self.log)
        self.assertFalse(state["ok"])
        self.assertEqual(state["brokenAt"], 3)
        self.assertIn("altered", state["reason"])

    def test_deleting_a_record_is_detected(self):
        for i in range(5):
            guards.chain_append(self.log, {"capability": f"cap{i}"})
        lines = self.log.read_text(encoding="utf-8").splitlines()
        del lines[2]
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state = guards.chain_verify(self.log)
        self.assertFalse(state["ok"])

    def test_tail_truncation_is_caught_by_a_checkpoint_and_not_by_the_chain_alone(self):
        """The honest limitation, asserted rather than glossed over.

        Whoever can append to the log can also cut its tail off and keep going with a
        chain that verifies perfectly. Only an external witness notices, which is what
        `harness checkpoint` is for.
        """
        for i in range(5):
            guards.chain_append(self.log, {"capability": f"cap{i}"})
        witness = Path(self.dir.name) / "checkpoints.jsonl"
        before = guards.chain_checkpoint(self.log, witness)

        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.log.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")

        after = guards.chain_verify(self.log)
        self.assertTrue(after["ok"], "a truncated chain still verifies - this is the gap")
        self.assertNotEqual(before["head"], after["head"],
                            "but the checkpointed head no longer matches, which is how you find out")

    def test_pre_chain_history_is_reported_as_uncovered_not_silently_trusted(self):
        self.log.write_text(json.dumps({"ts": "old", "capability": "legacy"}) + "\n", encoding="utf-8")
        guards.chain_append(self.log, {"capability": "new"})
        state = guards.chain_verify(self.log)
        self.assertTrue(state["ok"])
        self.assertEqual(state["legacy"], 1)
        self.assertEqual(state["chained"], 1)

    def test_checkpointing_a_broken_chain_is_refused(self):
        guards.chain_append(self.log, {"capability": "a"})
        self.log.write_text(self.log.read_text(encoding="utf-8").replace('"a"', '"b"'), encoding="utf-8")
        with self.assertRaises(guards.GuardError):
            guards.chain_checkpoint(self.log, Path(self.dir.name) / "cp.jsonl")


class LegacySealing(unittest.TestCase):
    """Pre-chain records cannot be retroactively chained without rewriting them, and
    rewriting an audit trail to make it verifiable is self-defeating. Sealing is the honest
    alternative: one MAC over the region, proving it has not changed *since the seal*."""

    KEY = b"test-key-not-a-real-one"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "audit.jsonl"
        self.seal = Path(self.dir.name) / "seal.json"
        # Three legacy records, then the chained era begins.
        with self.log.open("w", encoding="utf-8") as handle:
            for i in range(3):
                handle.write(json.dumps({"ts": "old", "capability": f"legacy{i}"}) + "\n")
        for i in range(2):
            guards.chain_append(self.log, {"capability": f"new{i}"})

    def tearDown(self):
        self.dir.cleanup()

    def test_the_legacy_region_is_the_unchained_prefix(self):
        count, blob = guards.legacy_region(self.log)
        self.assertEqual(count, 3)
        self.assertIn(b"legacy2", blob)
        self.assertNotIn(b"new0", blob, "the chained era must not be inside the seal")

    def test_an_unsealed_region_is_reported_as_unsealed(self):
        state = guards.verify_legacy_seal(self.log, self.KEY, self.seal)
        self.assertFalse(state["sealed"])
        self.assertEqual(state["legacyRecords"], 3)

    def test_a_sealed_region_verifies(self):
        guards.seal_legacy(self.log, self.KEY, self.seal)
        state = guards.verify_legacy_seal(self.log, self.KEY, self.seal)
        self.assertTrue(state["sealed"])
        self.assertTrue(state["intact"])

    def test_editing_a_legacy_record_breaks_the_seal(self):
        """The whole point. Before sealing, these records could be edited freely and
        `audit-verify` would still report ok, because the chain simply did not cover them."""
        guards.seal_legacy(self.log, self.KEY, self.seal)
        text = self.log.read_text(encoding="utf-8").replace("legacy1", "tampered")
        self.log.write_text(text, encoding="utf-8")
        state = guards.verify_legacy_seal(self.log, self.KEY, self.seal)
        self.assertFalse(state["intact"])
        self.assertIn("no longer matches its seal", state["reason"])

    def test_deleting_a_legacy_record_breaks_the_seal(self):
        guards.seal_legacy(self.log, self.KEY, self.seal)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        del lines[1]
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(guards.verify_legacy_seal(self.log, self.KEY, self.seal)["intact"])

    def test_appending_new_chained_records_does_not_break_the_seal(self):
        """Normal operation must not trip the alarm, or the alarm gets ignored."""
        guards.seal_legacy(self.log, self.KEY, self.seal)
        for i in range(5):
            guards.chain_append(self.log, {"capability": f"later{i}"})
        self.assertTrue(guards.verify_legacy_seal(self.log, self.KEY, self.seal)["intact"])

    def test_the_seal_cannot_be_recomputed_without_the_key(self):
        """An HMAC, not a bare hash. A plain hash stored next to the log is no defence:
        whoever edits a record can recompute it and update the anchor."""
        guards.seal_legacy(self.log, self.KEY, self.seal)
        text = self.log.read_text(encoding="utf-8").replace("legacy1", "tampered")
        self.log.write_text(text, encoding="utf-8")
        # An attacker without the key reseals with their own key...
        guards.seal_legacy(self.log, b"attacker-key", self.seal)
        # ...and the real key still reports the region as altered.
        self.assertFalse(guards.verify_legacy_seal(self.log, self.KEY, self.seal)["intact"])

    def test_a_log_with_no_legacy_records_needs_no_seal(self):
        fresh = Path(self.dir.name) / "fresh.jsonl"
        guards.chain_append(fresh, {"capability": "a"})
        state = guards.verify_legacy_seal(fresh, self.KEY, self.seal)
        self.assertEqual(state["legacyRecords"], 0)
        self.assertTrue(state["sealed"], "nothing to seal counts as sealed")

    def test_sealing_an_empty_region_is_refused_rather_than_faked(self):
        fresh = Path(self.dir.name) / "fresh2.jsonl"
        guards.chain_append(fresh, {"capability": "a"})
        with self.assertRaises(guards.GuardError):
            guards.seal_legacy(fresh, self.KEY, self.seal)

    def test_the_live_audit_trail_legacy_region_is_sealed_and_intact(self):
        """A regression guard on the real trail: once sealed, it must stay sealed."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import harness as h

        audit = ROOT / "memory" / "harness-audit.jsonl"
        if not audit.exists():
            self.skipTest("no audit trail yet")
        state = guards.verify_legacy_seal(audit, h.load_key(), h.SEAL_PATH)
        if state["legacyRecords"] == 0:
            self.skipTest("no legacy region on this clone")
        self.assertTrue(state["sealed"], "run 'harness seal-legacy'")
        self.assertTrue(state.get("intact"), state.get("reason"))


class ReviewLedger(unittest.TestCase):
    """`sign --review` can say what changed since the last signature. It cannot say whether
    that signature was itself reviewed — and the first one on any clone never was. The ledger
    makes that gap visible instead of leaving it to memory."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ledger = Path(self.dir.name) / "review-log.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_an_empty_ledger_reports_no_baseline(self):
        state = guards.review_history(self.ledger)
        self.assertEqual(state["signings"], 0)
        self.assertIsNone(state["baseline"])
        self.assertIsNone(state["baselineWasReviewed"])

    def test_reviewed_and_unreviewed_signings_are_counted_separately(self):
        guards.record_review(self.ledger, {"ts": "t1", "reviewed": False, "changes": 3})
        guards.record_review(self.ledger, {"ts": "t2", "reviewed": True, "changes": 1})
        guards.record_review(self.ledger, {"ts": "t3", "reviewed": True, "changes": 0})
        state = guards.review_history(self.ledger)
        self.assertEqual(state["signings"], 3)
        self.assertEqual(state["reviewed"], 2)
        self.assertEqual(state["unreviewed"], 1)

    def test_an_unreviewed_baseline_is_reported_as_such(self):
        """The honest answer to 'was the baseline vetted?' is often no, and saying so is the
        whole point of the ledger."""
        guards.record_review(self.ledger, {"ts": "t1", "reviewed": False})
        guards.record_review(self.ledger, {"ts": "t2", "reviewed": True})
        self.assertFalse(guards.review_history(self.ledger)["baselineWasReviewed"])

    def test_accepted_escalations_are_recorded_by_name(self):
        guards.record_review(self.ledger, {
            "ts": "t1", "reviewed": True,
            "acceptedEscalations": ["caller.local-model.grants.remember"],
        })
        state = guards.review_history(self.ledger)
        self.assertEqual(state["signingsWithAcceptedEscalations"], 1)
        self.assertIn("caller.local-model.grants.remember",
                      state["recent"][0]["acceptedEscalations"])

    def test_the_ledger_is_hash_chained(self):
        """A record of approvals that can be edited afterwards is a record of whatever the last
        editor preferred."""
        for i in range(3):
            guards.record_review(self.ledger, {"ts": f"t{i}", "reviewed": True})
        self.assertTrue(guards.review_history(self.ledger)["chainOk"])

        text = self.ledger.read_text(encoding="utf-8").replace('"t1"', '"forged"')
        self.ledger.write_text(text, encoding="utf-8")
        self.assertFalse(guards.review_history(self.ledger)["chainOk"])

    def test_the_live_ledger_verifies(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import harness as h

        if not h.REVIEW_LEDGER_PATH.exists():
            self.skipTest("no signings recorded on this clone yet")
        state = guards.review_history(h.REVIEW_LEDGER_PATH)
        self.assertTrue(state["chainOk"], "the signing history has been altered")
        self.assertGreater(state["signings"], 0)


class AdversarialSuiteExists(unittest.TestCase):
    """The unit suites test the mechanisms; `scripts/redteam_harness.py` tests the *claims*,
    by attempting each attack the documentation says is refused.

    A mechanism can keep working while the property it was supposed to give you quietly stops
    holding — a path check that still rejects bad paths after the capability stopped taking
    paths, a quota that still counts after the caller lost the capability it was bounding. So
    this asserts the adversarial suite is still present and still probing the things that
    matter, because a red team nobody runs is a red team that does not exist.
    """

    def setUp(self):
        self.script = ROOT / "scripts" / "redteam_harness.py"
        if not self.script.exists():
            self.fail("the adversarial suite is missing")
        self.text = self.script.read_text(encoding="utf-8")

    def test_it_probes_every_control_family(self):
        for control in ("signed-policy", "deny-by-default", "allowlist", "gate", "argv-only",
                        "path-confinement", "scoped-tokens", "revocation", "audit-chain",
                        "legacy-seal", "checkpoint", "quota", "reviewed-signing", "egress",
                        "policy-lint", "review-ledger", "confinement", "redaction", "audit"):
            with self.subTest(control=control):
                self.assertIn(f'"{control}"', self.text,
                              f"no probe references the {control} control")

    def test_it_restores_everything_it_mutates(self):
        """A red team that leaves the policy tampered with is an outage, not a test."""
        self.assertGreaterEqual(self.text.count("finally:"), 4)
        self.assertIn("policy_path.write_bytes(original)", self.text)

    def test_it_does_not_tamper_with_the_live_audit_trail(self):
        """Tamper-detection is tested on a copy. Corrupting the real record to prove you can
        detect corruption is not a trade worth making."""
        self.assertIn("TemporaryDirectory", self.text)
        self.assertIn("never the real trail", self.text)

    def test_it_pairs_negative_results_with_controls(self):
        """"The confined child failed" only means something beside "the unconfined one did not"."""
        self.assertIn("CONTROL:", self.text)

    def test_a_refusal_for_the_wrong_reason_is_not_a_pass(self):
        """This is what caught a stale probe: an attack refused one control earlier than the one
        being tested would otherwise have looked like the control working."""
        self.assertIn("not for the expected reason", self.text)


class Redaction(unittest.TestCase):
    def test_non_allowlisted_values_are_withheld(self):
        out = guards.redact({"path": "C:/Alfred/x", "apiKey": "abcd"}, ["path"])
        self.assertEqual(out["path"], "C:/Alfred/x")
        self.assertEqual(out["apiKey"], guards.REDACTED)

    def test_names_are_kept_so_the_audit_stays_useful(self):
        out = guards.redact({"token": "hunter2"}, ["path"])
        self.assertIn("token", out)

    def test_a_secret_shaped_value_is_withheld_even_from_an_allowlisted_field(self):
        """Allowlist first, then a sanity check. An allowlisted field carrying a
        64-character hex blob is a signing key someone put in the wrong parameter."""
        out = guards.redact({"query": "a" * 64}, ["query"])
        self.assertEqual(out["query"], guards.REDACTED)

    def test_a_new_parameter_is_withheld_by_default(self):
        """The whole point of allowlisting: tomorrow's parameter is safe today."""
        out = guards.redact({"brandNewThing": "whatever"}, guards.DEFAULT_LOGGABLE_PARAMS)
        self.assertEqual(out["brandNewThing"], guards.REDACTED)


class RateLimit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name) / "quota.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_burst_is_allowed_then_the_bucket_runs_dry(self):
        for i in range(5):
            guards.Quota(self.state, now=1000.0).check_and_consume("local-model", 60, burst=5)
        with self.assertRaises(guards.GuardError) as ctx:
            guards.Quota(self.state, now=1000.0).check_and_consume("local-model", 60, burst=5)
        self.assertIn("rate limit exceeded", str(ctx.exception))

    def test_the_bucket_refills_over_time(self):
        for _ in range(5):
            guards.Quota(self.state, now=1000.0).check_and_consume("local-model", 60, burst=5)
        # 60/min = 1/sec, so two seconds later there are two tokens again.
        usage = guards.Quota(self.state, now=1002.0).check_and_consume("local-model", 60, burst=5)
        self.assertTrue(usage["limited"])

    def test_callers_have_independent_buckets(self):
        for _ in range(3):
            guards.Quota(self.state, now=1000.0).check_and_consume("local-model", 60, burst=3)
        # A different caller must not be affected by the first one's exhaustion.
        usage = guards.Quota(self.state, now=1000.0).check_and_consume("scheduled", 60, burst=3)
        self.assertTrue(usage["limited"])

    def test_no_limit_configured_means_no_limiting(self):
        result = guards.Quota(self.state).check_and_consume("owner", 0)
        self.assertFalse(result["limited"])

    def test_a_corrupt_quota_file_does_not_deny_everything(self):
        """Fail-closed is right for authorization and wrong for a quota file: a torn
        write must not lock the Owner out of his own machine. It resets and repairs."""
        self.state.write_text("{not json", encoding="utf-8")
        usage = guards.Quota(self.state, now=1000.0).check_and_consume("local-model", 60, burst=5)
        self.assertTrue(usage["limited"])
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8")))


class ScopedTokenUnits(unittest.TestCase):
    KEY = b"test-key-not-a-real-one"

    def test_a_valid_token_verifies_for_its_scope(self):
        token = guards.mint_token(self.KEY, "local-model", ["status"], 60, now=1000.0)
        claims = guards.verify_token(self.KEY, token, "local-model", "status", now=1000.0)
        self.assertEqual(claims["scopes"], ["status"])

    def test_widening_the_scope_invalidates_the_token(self):
        """The scopes are inside the MAC, so editing them is forgery, not escalation."""
        token = guards.mint_token(self.KEY, "local-model", ["status"], 60, now=1000.0)
        forged = token.replace(".status.", ".status+backup.")
        with self.assertRaises(guards.GuardError) as ctx:
            guards.verify_token(self.KEY, forged, "local-model", "backup", now=1000.0)
        self.assertIn("signature", str(ctx.exception))

    def test_pushing_the_expiry_out_invalidates_the_token(self):
        token = guards.mint_token(self.KEY, "local-model", ["status"], 1, now=1000.0)
        parts = token.split(".")
        parts[2] = "9999999999"
        with self.assertRaises(guards.GuardError) as ctx:
            guards.verify_token(self.KEY, ".".join(parts), "local-model", "status", now=2000.0)
        self.assertIn("signature", str(ctx.exception))

    def test_a_different_key_cannot_mint_an_acceptable_token(self):
        token = guards.mint_token(b"attacker-key", "local-model", ["status"], 60, now=1000.0)
        with self.assertRaises(guards.GuardError):
            guards.verify_token(self.KEY, token, "local-model", "status", now=1000.0)

    def test_each_token_is_unique(self):
        a = guards.mint_token(self.KEY, "local-model", ["status"], 60, now=1000.0)
        b = guards.mint_token(self.KEY, "local-model", ["status"], 60, now=1000.0)
        self.assertNotEqual(a, b, "a nonce makes two tokens minted in the same second distinct")

    def test_a_wildcard_token_covers_any_capability(self):
        token = guards.mint_token(self.KEY, "owner", [], 60, now=1000.0)
        claims = guards.verify_token(self.KEY, token, "owner", "anything", now=1000.0)
        self.assertEqual(claims["scopes"], ["*"])

    def test_a_token_carries_its_issue_time(self):
        """Needed for whole-caller revocation; asserted so it cannot be dropped."""
        token = guards.mint_token(self.KEY, "owner", ["status"], 60, now=1000.0)
        claims = guards.verify_token(self.KEY, token, "owner", "status", now=1000.0)
        self.assertEqual(claims["iat"], 1000)
        self.assertEqual(claims["exp"], 1060)

    def test_editing_the_issue_time_invalidates_the_token(self):
        """Otherwise a revoked generation could re-date itself out of the revocation."""
        token = guards.mint_token(self.KEY, "agent", ["status"], 60, now=1000.0)
        parts = token.split(".")
        parts[2] = "9999999999"
        with self.assertRaises(guards.GuardError) as ctx:
            guards.verify_token(self.KEY, ".".join(parts), "agent", "status", now=1000.0)
        self.assertIn("signature", str(ctx.exception))


class Revocation(unittest.TestCase):
    """Expiry alone is not revocation. A token that leaks at minute one of a one-hour TTL
    is usable for 59 minutes unless something can say 'not that one'."""

    KEY = b"test-key-not-a-real-one"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Path(self.dir.name) / "revoked.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_revoked_token_stops_working(self):
        token = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1000.0)
        claims = guards.token_claims(self.KEY, token)
        # Valid before revocation.
        guards.verify_token(self.KEY, token, "agent", "status", now=1010.0,
                            revocations=guards.load_revocations(self.store, now=1010.0))
        guards.revoke_nonce(self.store, claims["nonce"], claims["exp"], now=1010.0)
        with self.assertRaises(guards.GuardError) as ctx:
            guards.verify_token(self.KEY, token, "agent", "status", now=1020.0,
                                revocations=guards.load_revocations(self.store, now=1020.0))
        self.assertIn("revoked", str(ctx.exception))

    def test_revoking_one_token_does_not_affect_another(self):
        a = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1000.0)
        b = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1000.0)
        guards.revoke_nonce(self.store, guards.token_claims(self.KEY, a)["nonce"], 1600, now=1010.0)
        revs = guards.load_revocations(self.store, now=1010.0)
        with self.assertRaises(guards.GuardError):
            guards.verify_token(self.KEY, a, "agent", "status", now=1010.0, revocations=revs)
        self.assertTrue(guards.verify_token(self.KEY, b, "agent", "status", now=1010.0,
                                            revocations=revs))

    def test_revoking_a_caller_kills_every_existing_token(self):
        """The break-glass case: a credential leaked and you do not know which one."""
        old_a = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1000.0)
        old_b = guards.mint_token(self.KEY, "agent", ["disk-report"], 600, now=1050.0)
        guards.revoke_caller(self.store, "agent", now=1100.0)
        revs = guards.load_revocations(self.store, now=1100.0)
        for token, scope in ((old_a, "status"), (old_b, "disk-report")):
            with self.subTest(token=scope):
                with self.assertRaises(guards.GuardError) as ctx:
                    guards.verify_token(self.KEY, token, "agent", scope, now=1100.0,
                                        revocations=revs)
                self.assertIn("was revoked", str(ctx.exception))

    def test_a_token_minted_in_the_same_second_as_the_revocation_is_still_revoked(self):
        """Regression. Found by demonstrating the feature rather than by a test.

        Timestamps have one-second resolution, so a token minted in the same second as
        the revocation had `iat == epoch` and survived a `iat < epoch` check — and that is
        exactly the token most likely to be the leaked one, because a leak and the
        response to it happen close together. The epoch is now stored as `now + 1`, which
        errs toward revoking too much.
        """
        token = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1000.0)
        guards.revoke_caller(self.store, "agent", now=1000.0)
        with self.assertRaises(guards.GuardError) as ctx:
            guards.verify_token(self.KEY, token, "agent", "status", now=1001.0,
                                revocations=guards.load_revocations(self.store, now=1001.0))
        self.assertIn("was revoked", str(ctx.exception))

    def test_a_token_minted_after_a_caller_revocation_still_works(self):
        """Revoking a caller must not permanently disable it — otherwise the response to
        a leak is an outage. The epoch is `now + 1`, so a replacement minted in the next
        second onward is accepted."""
        guards.revoke_caller(self.store, "agent", now=1100.0)
        fresh = guards.mint_token(self.KEY, "agent", ["status"], 600, now=1101.0)
        claims = guards.verify_token(self.KEY, fresh, "agent", "status", now=1102.0,
                                     revocations=guards.load_revocations(self.store, now=1102.0))
        self.assertEqual(claims["scopes"], ["status"])

    def test_revoking_one_caller_does_not_affect_another(self):
        guards.revoke_caller(self.store, "agent", now=1100.0)
        other = guards.mint_token(self.KEY, "scheduled", ["status"], 600, now=1000.0)
        self.assertTrue(guards.verify_token(self.KEY, other, "scheduled", "status", now=1100.0,
                                            revocations=guards.load_revocations(self.store, now=1100.0)))

    def test_expired_entries_are_pruned_on_read(self):
        """The list is read on every authenticated call, so it must not grow forever. A
        revoked nonce is redundant once the token would have expired anyway."""
        guards.revoke_nonce(self.store, "short-lived", 1200, now=1000.0)
        guards.revoke_nonce(self.store, "long-lived", 9999, now=1000.0)
        later = guards.load_revocations(self.store, now=5000.0)
        self.assertNotIn("short-lived", later["nonces"])
        self.assertIn("long-lived", later["nonces"])

    def test_a_missing_store_means_nothing_is_revoked(self):
        self.assertEqual(guards.load_revocations(self.store)["nonces"], {})

    def test_a_corrupt_store_does_not_lock_everyone_out(self):
        """Fail-open here is deliberate and narrow: the file lives in secrets/ where no
        agent can reach it, so its integrity is protected by the directory. Treating a
        torn write as 'everything is revoked' would turn one bad write into an outage."""
        self.store.write_text("{not json", encoding="utf-8")
        self.assertEqual(guards.load_revocations(self.store)["nonces"], {})

    def test_an_unverified_token_cannot_be_revoked(self):
        """Otherwise anyone could stuff invented nonces into a file that is read on every
        authenticated call."""
        with self.assertRaises(guards.GuardError):
            guards.token_claims(self.KEY, "aht2.agent.1.2.nonce.status." + "0" * 32)


class PolicyLint(unittest.TestCase):
    BASE = {
        "settings": {"denyByDefault": True, "requireSignature": True,
                     "maxOutputBytes": 1024, "allowedWorkspaceRoots": ["C:/Alfred"]},
        "callers": {"owner": {"trust": "high", "capabilities": ["*"]}},
        "capabilities": {"status": {"risk": "read", "description": "d", "command": "x",
                                    "args": [], "network": False}},
    }

    def _lint(self, mutate) -> list[dict[str, str]]:
        policy = json.loads(json.dumps(self.BASE))
        mutate(policy)
        return guards.lint_policy(policy)

    def test_a_healthy_policy_has_no_findings(self):
        self.assertEqual(guards.lint_policy(json.loads(json.dumps(self.BASE))), [])

    def test_a_grant_of_an_undefined_capability_is_an_error(self):
        def mutate(p):
            p["callers"]["bot"] = {"trust": "low", "authRequired": True, "capabilities": ["ghost"]}
        findings = self._lint(mutate)
        self.assertTrue(any("not a defined capability" in f["message"] for f in findings))

    def test_a_gated_capability_granted_to_low_trust_is_an_error(self):
        """This is the finding that caught a real dead grant in Alfred's own policy:
        `kiro-agent` (medium) held `ultron-pipeline` (gated, high-trust-only), so the
        allowlist advertised a power the gate refused every single time."""
        def mutate(p):
            p["capabilities"]["deploy"] = {"risk": "write", "gated": True, "description": "d",
                                           "command": "x", "args": []}
            p["callers"]["bot"] = {"trust": "medium", "capabilities": ["deploy"]}
        findings = self._lint(mutate)
        self.assertTrue(any("gate would always refuse" in f["message"] for f in findings))

    def test_a_write_capability_granted_to_an_untrusted_caller_is_an_error(self):
        def mutate(p):
            p["capabilities"]["mutate"] = {"risk": "write", "description": "d", "command": "x", "args": []}
            p["callers"]["bot"] = {"trust": "untrusted", "authRequired": True, "capabilities": ["mutate"]}
        findings = self._lint(mutate)
        self.assertTrue(any("untrusted caller" in f["message"] for f in findings))

    def test_an_undeclared_argv_placeholder_is_an_error(self):
        def mutate(p):
            p["capabilities"]["status"]["args"] = ["--path", "{path}"]
        findings = self._lint(mutate)
        self.assertTrue(any("does not declare it as a param" in f["message"] for f in findings))

    def test_a_declared_but_unused_param_is_a_warning(self):
        """A param that never reaches argv is never validated either - it looks like a
        control and is not one."""
        def mutate(p):
            p["capabilities"]["status"]["params"] = {"unused": {"type": "string"}}
        findings = self._lint(mutate)
        self.assertTrue(any("never uses it in argv" in f["message"] for f in findings))

    def test_a_capability_nobody_can_run_is_a_warning(self):
        def mutate(p):
            p["callers"]["owner"]["capabilities"] = ["status"]
            p["capabilities"]["orphan"] = {"risk": "read", "description": "d", "command": "x", "args": []}
        findings = self._lint(mutate)
        self.assertTrue(any("dead entry" in f["message"] for f in findings))

    def test_a_wildcard_granted_to_non_high_trust_is_an_error(self):
        def mutate(p):
            p["callers"]["bot"] = {"trust": "low", "authRequired": True, "capabilities": ["*"]}
        findings = self._lint(mutate)
        self.assertTrue(any("wildcard" in f["message"] for f in findings))

    def test_disabling_a_core_setting_is_an_error(self):
        for key in ("denyByDefault", "requireSignature"):
            with self.subTest(setting=key):
                findings = self._lint(lambda p, k=key: p["settings"].__setitem__(k, False))
                self.assertTrue(
                    any(f["level"] == "error" and key in f["message"] for f in findings),
                    f"disabling {key} must be an error",
                )


class TrailingDotExemption(unittest.TestCase):
    def test_dot_and_dotdot_components_are_allowed_through(self):
        """They are the two components that legitimately consist of dots. Refusing
        them would break ordinary relative paths and buy nothing: realpath collapses
        them, and confinement judges the real target."""
        resolved = guards.safe_resolve("scripts/../scripts/harness.py", ROOT)
        self.assertTrue(resolved.exists())
        self.assertTrue(guards.inside_roots(resolved, [str(ROOT)]))


class LiveHarnessPolicyIsClean(unittest.TestCase):
    def test_alfreds_own_policy_passes_the_lint(self):
        """A regression guard on the policy itself, not just on the linter."""
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        errors = [f for f in guards.lint_policy(policy) if f["level"] == "error"]
        self.assertEqual(errors, [], f"policy lint errors: {errors}")


@unittest.skipUnless(WINDOWS, "Job Objects are a Windows facility")
class JobObjectConfinement(unittest.TestCase):
    """Memory and CPU are the two resources a runaway child exhausts first, and
    `subprocess` bounds neither. These tests spend real memory to prove the ceiling is
    enforced by the kernel rather than merely written into a config file."""

    def test_a_child_cannot_exceed_the_memory_limit(self):
        limits = confine.Limits(memory_bytes=64 * 1024 * 1024)
        code = "b = bytearray(400 * 1024 * 1024); print(len(b))"
        proc, job, note = confine.spawn_confined([sys.executable, "-c", code], limits)
        out, err = proc.communicate(timeout=60)
        usage = confine.query_job(job)
        confine.close_job(job)

        self.assertEqual(note, "confined")
        self.assertNotEqual(proc.returncode, 0, "a 400MB allocation under a 64MB cap must fail")
        self.assertIn(b"MemoryError", err)
        self.assertLess(usage["peakProcessBytes"], 64 * 1024 * 1024,
                        "the child must never have been given the memory it asked for")

    def test_the_same_allocation_succeeds_without_a_limit(self):
        """The control half of the experiment. Without this, the test above could be
        passing because the allocation fails for some unrelated reason."""
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", "b = bytearray(400 * 1024 * 1024); print(len(b))"],
            confine.Limits(),
        )
        out, _ = proc.communicate(timeout=60)
        confine.close_job(job)
        self.assertEqual(note, "no limits configured")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn(b"419430400", out)

    def test_a_confined_child_still_runs_normally(self):
        """The suspend/assign/resume dance must not leave the child suspended forever.

        This is the test that would catch the whole mechanism deadlocking - which is the
        most likely way a race-free implementation goes wrong.
        """
        limits = confine.Limits(memory_bytes=256 * 1024 * 1024, active_processes=8)
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", "print('hello from a confined child')"], limits)
        out, err = proc.communicate(timeout=60)
        confine.close_job(job)
        self.assertEqual(note, "confined")
        self.assertEqual(proc.returncode, 0, err)
        self.assertIn(b"hello from a confined child", out)

    def test_closing_the_job_kills_the_tree(self):
        """The property subprocess cannot replicate.

        Killing a child on timeout leaves its grandchildren running, unparented and
        outside the audit trail. KILL_ON_JOB_CLOSE means the tree cannot outlive the job
        handle - so it cannot outlive the harness, even if the harness is killed rather
        than exiting cleanly.
        """
        limits = confine.Limits(memory_bytes=256 * 1024 * 1024)
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", "import time; time.sleep(120)"], limits)
        self.assertEqual(note, "confined")
        self.assertIsNone(proc.poll(), "the child should still be running")

        confine.close_job(job)

        for _ in range(100):          # up to ~5s for the kernel to reap it
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll(), "closing the job must terminate the child")
        proc.wait(timeout=10)

    def test_the_active_process_limit_blocks_a_fork_bomb(self):
        limits = confine.Limits(memory_bytes=512 * 1024 * 1024, active_processes=1)
        # The child is the one permitted process, so its own attempt to spawn must fail.
        code = (
            "import subprocess, sys\n"
            "try:\n"
            "    subprocess.run([sys.executable, '-c', 'pass'], timeout=30)\n"
            "    print('SPAWNED')\n"
            "except Exception as e:\n"
            "    print('BLOCKED', type(e).__name__)\n"
        )
        proc, job, _ = confine.spawn_confined([sys.executable, "-c", code], limits)
        out, err = proc.communicate(timeout=60)
        confine.close_job(job)
        self.assertNotIn(b"SPAWNED", out,
                         f"a child at the process cap must not spawn another: {out!r} {err!r}")

    def test_peak_usage_is_reported_for_the_audit(self):
        """A limit you cannot observe is a limit you cannot audit."""
        limits = confine.Limits(memory_bytes=512 * 1024 * 1024)
        code = "b = bytearray(100 * 1024 * 1024); print(len(b))"
        proc, job, _ = confine.spawn_confined([sys.executable, "-c", code], limits)
        proc.communicate(timeout=60)
        usage = confine.query_job(job)
        confine.close_job(job)
        self.assertGreater(usage["peakProcessBytes"], 90 * 1024 * 1024)
        self.assertGreaterEqual(usage["totalProcesses"], 1)

    def test_io_and_cpu_are_accounted(self):
        """Disk activity is *accounted*, not bounded — Job Objects cannot cap total bytes
        written, so a capability permitted to write can still fill a disk. Recording the
        figure at least makes that visible after the fact, which is the honest position."""
        limits = confine.Limits(memory_bytes=512 * 1024 * 1024)
        code = (
            "import os, tempfile\n"
            "p = os.path.join(tempfile.gettempdir(), 'harness-io-probe.bin')\n"
            "open(p, 'wb').write(b'x' * 3_000_000)\n"
            "os.unlink(p)\n"
        )
        proc, job, _ = confine.spawn_confined([sys.executable, "-c", code], limits)
        proc.communicate(timeout=60)
        usage = confine.query_job(job)
        confine.close_job(job)
        self.assertGreaterEqual(usage["bytesWritten"], 3_000_000,
                                "the job must account for what its tree wrote")
        self.assertIn("cpuSeconds", usage)
        self.assertGreaterEqual(usage["cpuSeconds"], 0)

    def test_querying_or_closing_a_missing_job_is_safe(self):
        """The unconfined path returns None, and the caller should not have to special-case
        it on every exit route."""
        self.assertEqual(confine.query_job(None), {})
        confine.close_job(None)   # must not raise

    def test_usage_must_be_read_before_the_handle_is_closed(self):
        """Closing the job destroys its accounting. Documented as an ordering
        requirement in execute(); asserted here so a refactor cannot quietly reverse it."""
        limits = confine.Limits(memory_bytes=256 * 1024 * 1024)
        proc, job, _ = confine.spawn_confined([sys.executable, "-c", "pass"], limits)
        proc.communicate(timeout=30)
        confine.close_job(job)
        self.assertEqual(confine.query_job(job), {},
                         "a closed job yields nothing, which is why execute() reads first")


class ConfinementLimitsFromPolicy(unittest.TestCase):
    SETTINGS = {
        "confinement": {"maxMemoryBytes": 2048, "maxActiveProcesses": 64},
        "confinementByTrust": {"untrusted": {"maxMemoryBytes": 256, "maxActiveProcesses": 8}},
    }

    def test_trust_narrows_the_default(self):
        base = confine.limits_from_policy(self.SETTINGS)
        tight = confine.limits_from_policy(self.SETTINGS, "untrusted")
        self.assertEqual(base.memory_bytes, 2048)
        self.assertEqual(tight.memory_bytes, 256)
        self.assertEqual(tight.active_processes, 8)

    def test_an_unlisted_trust_level_falls_back_to_the_default(self):
        limits = confine.limits_from_policy(self.SETTINGS, "medium")
        self.assertEqual(limits.memory_bytes, 2048)

    def test_no_settings_means_no_limits(self):
        self.assertFalse(confine.limits_from_policy({}).any_set())

    def test_zero_is_not_treated_as_a_limit(self):
        """Windows treats a zero limit as 'no limit'. Conflating the two would silently
        disable a control someone believed they had configured, so `any_set` agrees with
        the kernel rather than with Python's truthiness by accident."""
        limits = confine.limits_from_policy({"confinement": {"maxMemoryBytes": 0}})
        self.assertFalse(limits.any_set())

    def test_the_live_policy_confines_the_untrusted_caller_hardest(self):
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        settings = policy["settings"]
        untrusted = confine.limits_from_policy(settings, "untrusted")
        high = confine.limits_from_policy(settings, "high")
        self.assertTrue(untrusted.any_set(), "the untrusted caller must be confined")
        self.assertLess(untrusted.memory_bytes, high.memory_bytes)
        self.assertLess(untrusted.active_processes, high.active_processes)
        self.assertLess(untrusted.cpu_seconds, high.cpu_seconds)

    def test_a_file_size_limit_is_carried_through(self):
        """POSIX-only, but the policy and the Limits object must carry it on any platform,
        or the same signed policy would mean different things on different machines."""
        limits = confine.limits_from_policy(
            {"confinement": {"maxFileBytes": 12345}})
        self.assertEqual(limits.max_file_bytes, 12345)
        self.assertTrue(limits.any_set(), "a file-size limit alone still counts as confined")

    def test_the_live_policy_sets_a_file_size_limit(self):
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        untrusted = confine.limits_from_policy(policy["settings"], "untrusted")
        self.assertTrue(untrusted.max_file_bytes,
                        "the untrusted caller should have a disk bound where the OS allows one")


class NetworkIsolation(unittest.TestCase):
    """Egress is a declared property of each capability, enforced where the kernel allows.

    On Linux a `network: false` capability runs in a fresh network namespace and cannot reach
    anything — including loopback, because a new namespace's `lo` is DOWN. On Windows there is
    no equivalent short of AppContainer, so the declaration is recorded in the audit trail and
    the code must say which of the two happened rather than implying the stronger one.
    """

    def test_availability_is_reported_honestly_for_this_platform(self):
        available = confine.network_isolation_available()
        if WINDOWS:
            self.assertFalse(available, "Windows has no unprivileged network namespace")
        else:
            self.assertIsInstance(available, bool)

    def test_requesting_isolation_where_it_is_unavailable_still_runs_the_child(self):
        """A declaration that cannot be enforced must not become a refusal to work — but the
        note has to admit it, or a reader would assume egress was blocked."""
        proc, job, note = confine.spawn_confined(
            [sys.executable, "-c", "print('ran')"],
            confine.Limits(memory_bytes=256 * 1024 * 1024),
            isolate_network=True)
        out, err = proc.communicate(timeout=60)
        confine.close_job(job)
        self.assertIn(b"ran", out, err)
        if not confine.network_isolation_available():
            self.assertNotIn("netns", note.replace("netns-unavailable", ""),
                             "must not claim isolation it did not apply")

    def test_the_unshare_form_keeps_the_child_uid(self):
        """`--map-root-user` would make the child believe it is uid 0. Harmless on the host,
        but a script branching on geteuid()==0 would take a privileged path it should not."""
        self.assertIn("--map-current-user", confine.NETNS_PREFIX)
        self.assertNotIn("--map-root-user", confine.NETNS_PREFIX)
        self.assertIn("--user", confine.NETNS_PREFIX,
                      "a user namespace is what makes this work without root")

    def test_every_capability_declares_whether_it_needs_the_network(self):
        """An undeclared capability defaults to permissive, so silence is not a decision."""
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        undeclared = sorted(n for n, s in policy["capabilities"].items() if "network" not in s)
        self.assertEqual(undeclared, [], f"these do not declare `network`: {undeclared}")

    def test_most_capabilities_are_declared_local_only(self):
        """If almost everything needed the network the declaration would buy nothing."""
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        caps = policy["capabilities"]
        local = [n for n, s in caps.items() if not s.get("network", True)]
        self.assertGreater(len(local), len(caps) // 2,
                           "the majority of capabilities should be egress-isolated")

    def test_the_untrusted_callers_network_reach_is_minimal_and_justified(self):
        """A regression guard on the one accepted exposure. `graph-recall` embeds its query
        via LM Studio on loopback, which is why it is the exception; anything else appearing
        here is a change that needs a reason."""
        policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
        caps = policy["capabilities"]
        for name, spec in policy["callers"].items():
            if spec.get("trust") != "untrusted":
                continue
            reach = sorted(c for c in spec.get("capabilities", [])
                           if c in caps and caps[c].get("network", True))
            self.assertEqual(reach, ["graph-recall"],
                             f"{name}'s network reach changed: {reach}")


class NetworkDeclarationIsReviewed(unittest.TestCase):
    """The diff must see the egress declaration. It did not at first, which meant declaring
    `network` on fourteen capabilities showed up as "1 addition, 0 changes" — a review tool
    that cannot see a change gives false assurance, which is worse than no review tool."""

    BASE = {
        "settings": {"denyByDefault": True, "requireSignature": True,
                     "maxOutputBytes": 1024, "allowedWorkspaceRoots": ["C:/Alfred"]},
        "callers": {"owner": {"trust": "high", "capabilities": ["*"]}},
        "capabilities": {"probe": {"risk": "read", "description": "d", "command": "x",
                                   "args": [], "network": False}},
    }

    def test_granting_network_access_is_a_privilege_increase(self):
        after = json.loads(json.dumps(self.BASE))
        after["capabilities"]["probe"]["network"] = True
        delta = guards.diff_policy(self.BASE, after)
        reasons = {e["why"] for e in delta["privilegeIncreases"]}
        self.assertIn("a capability was granted network access", reasons)

    def test_removing_network_access_is_not_a_privilege_increase(self):
        before = json.loads(json.dumps(self.BASE))
        before["capabilities"]["probe"]["network"] = True
        delta = guards.diff_policy(before, self.BASE)
        self.assertEqual(delta["privilegeIncreases"], [], delta)
        self.assertTrue(any(c["key"].endswith(".network") for c in delta["changed"]))

    def test_an_undeclared_network_field_is_linted(self):
        policy = json.loads(json.dumps(self.BASE))
        del policy["capabilities"]["probe"]["network"]
        messages = [f["message"] for f in guards.lint_policy(policy)]
        self.assertTrue(any("does not declare `network`" in m for m in messages), messages)


class ConfinementIsHonestAboutThePlatform(unittest.TestCase):
    """The two platforms give genuinely different guarantees. The code must report which
    it applied rather than implying one story everywhere."""

    def test_the_module_names_the_mechanism_it_used(self):
        """`note` is how the caller (and the audit trail) learns whether the run was
        confined at all, and by what."""
        proc, job, note = confine.spawn_confined([sys.executable, "-c", "pass"],
                                                 confine.Limits())
        proc.communicate(timeout=30)
        confine.close_job(job)
        self.assertIn(note, ("no limits configured", "confined",
                             "no resource module: rlimit confinement unavailable"))

    def test_the_posix_verification_script_exists(self):
        """The POSIX branch cannot be exercised on Windows, so it is verified under WSL by
        a script that spends real resources. Keeping a test that asserts the script exists
        means the verification path cannot be quietly deleted, leaving the branch
        unverified while the suite still looks green."""
        script = ROOT / "scripts" / "verify_posix_confine.py"
        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        for needle in ("RLIMIT", "CONTROL:", "cannot raise its own limit"):
            self.assertIn(needle, text, f"the POSIX verifier must still check {needle}")

    @unittest.skipIf(WINDOWS, "POSIX branch")
    def test_posix_limits_are_built_from_the_policy(self):
        preexec = confine._posix_preexec(confine.Limits(memory_bytes=1024, cpu_seconds=2))
        self.assertTrue(callable(preexec))


if __name__ == "__main__":
    unittest.main(verbosity=2)
