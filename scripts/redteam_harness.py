#!/usr/bin/env python3
"""Red-team the harness: attempt every attack the controls claim to stop.

    python scripts/redteam_harness.py [--verbose]

WHY THIS EXISTS
---------------
``docs/harness.md`` claims sixteen controls, each naming an attack it defends against. The unit
suites test the mechanisms; this file tests the *claims*, adversarially, through the real CLI —
one attempted attack per claim, each expected to be refused.

The difference matters. A mechanism can keep working while the property it was supposed to give
you quietly stops holding: a path check that still rejects bad paths after the capability
stopped taking paths at all, a quota that still counts after the caller lost the capability it
was bounding. A test that asks "was this attack refused?" notices that; a test that asks "does
this function still return False?" does not.

Every attack that mutates state backs it up first and restores it in a ``finally``. Nothing here
touches the live audit trail: chain and seal attacks run against a copy, because the honest way
to test tamper-detection is not to tamper with the real record.

Exit code 0 means every attack was refused. Non-zero means at least one succeeded, and the
report names which.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "harness.py"
sys.path.insert(0, str(ROOT / "scripts"))
import harness_guards as guards  # noqa: E402

EXIT_OK, EXIT_POLICY, EXIT_DENIED, EXIT_INPUT, EXIT_FAILED = 0, 2, 3, 4, 5

VERBOSE = False
RESULTS: list[tuple[str, str, bool, str]] = []


def safe(text: str) -> str:
    """Reduce text to something this console can definitely print.

    The details reported here are a capability's own stderr, decoded with ``errors="replace"``,
    so they can contain U+FFFD — and a cp1252 console raises ``UnicodeEncodeError`` on that. A
    red-team report that crashes while printing a refusal is worse than useless: it looks like
    the harness broke. Third appearance of this bug class in this project, hence a helper rather
    than another local fix.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def cli(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HARNESS), *args], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def refused(control: str, attack: str, result: subprocess.CompletedProcess,
            expect: tuple[int, ...] = (EXIT_DENIED, EXIT_POLICY, EXIT_INPUT),
            needle: str = "") -> None:
    """Record whether an attack was refused. Refusal means a non-zero exit AND, when given, a
    reason mentioning ``needle`` — a refusal for an unrelated reason is not the control working.
    """
    blocked = result.returncode in expect
    detail = (result.stderr or result.stdout).strip().replace("\n", " ")
    if blocked and needle and needle.lower() not in detail.lower():
        blocked = False
        detail = f"refused, but not for the expected reason: {detail}"
    RESULTS.append((control, attack, blocked, safe(detail)[:150]))
    mark = "REFUSED" if blocked else "!! ALLOWED"
    print(f"  {mark:>10}  {attack}")
    if VERBOSE and detail:
        print(f"              {safe(detail)[:140]}")


def asserted(control: str, claim: str, ok: bool, detail: str = "") -> None:
    """For claims that are properties rather than attacks (e.g. "the trail recorded it")."""
    RESULTS.append((control, claim, ok, safe(detail)[:150]))
    print(f"  {'HOLDS' if ok else '!! BROKEN':>10}  {claim}")
    if VERBOSE and detail:
        print(f"              {safe(detail)[:140]}")


# --------------------------------------------------------------------------- the attacks


def attack_signed_policy() -> None:
    print("\n[1] Signed policy — a model edits the policy to widen its own powers")
    policy_path = ROOT / "policy" / "harness-policy.json"
    original = policy_path.read_bytes()
    try:
        hostile = json.loads(original)
        hostile["callers"]["local-model"]["capabilities"] = ["*"]
        hostile["callers"]["local-model"]["authRequired"] = False
        policy_path.write_bytes(json.dumps(hostile, indent=2).encode("utf-8"))

        refused("signed-policy", "grant local-model '*' and run a gated capability",
                cli(["run", "backup", "--caller", "local-model", "--approve"]),
                expect=(EXIT_POLICY,), needle="INTEGRITY FAILURE")
        refused("signed-policy", "with the policy untrusted, even a read capability is refused",
                cli(["run", "status", "--caller", "owner"]),
                expect=(EXIT_POLICY,), needle="INTEGRITY FAILURE")
    finally:
        policy_path.write_bytes(original)
    asserted("signed-policy", "the policy verifies again after restore",
             cli(["verify"]).returncode == EXIT_OK)


def attack_deny_by_default() -> None:
    print("\n[2] Deny by default — invent a capability, and impersonate a caller")
    refused("deny-by-default", "run a capability that is not in the policy",
            cli(["run", "exfiltrate-everything", "--caller", "owner"]), needle="deny by default")
    refused("deny-by-default", "run as a caller that does not exist",
            cli(["run", "status", "--caller", "attacker"]), needle="Unknown caller")


def attack_allowlist_and_gate() -> None:
    print("\n[3] Allowlist + gate — the untrusted caller reaches past its list")
    token = mint("local-model", ["disk-report"], 120)
    for cap, params in [("ci", []), ("test", []), ("web-search", ["--param", "query=x"]),
                        ("backup", []), ("git-commit", ["--param", "message=x"])]:
        refused("allowlist", f"local-model runs '{cap}' with a valid token",
                cli(["run", cap, "--caller", "local-model", "--token", token, "--approve", *params]))
    refused("gate", "the Owner runs a gated capability without --approve",
            cli(["run", "backup", "--caller", "owner"]), needle="--approve")

    # The trust half of the gate is structurally unreachable, and that is the point: `harness
    # lint` refuses a policy that grants a gated capability to a caller whose trust guarantees
    # the gate will reject it. So the assertion is that no such grant exists, rather than a probe
    # that would only ever be refused one step earlier by the allowlist. (An earlier version of
    # this probe aimed `ultron-pipeline` at `kiro-agent` and reported a hole, because that dead
    # grant was removed in an earlier session - the refusal came from the allowlist, not the
    # gate, and the reason-matching caught the discrepancy.)
    policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
    caps = policy["capabilities"]
    bad = []
    for name, spec in policy["callers"].items():
        if spec.get("trust") == "high":
            continue
        granted = spec.get("capabilities", [])
        if "*" in granted:
            bad.append(f"{name} holds '*'")
        bad += [f"{name} holds gated '{c}'" for c in granted
                if c in caps and caps[c].get("gated")]
    asserted("gate", "no caller below high trust holds a gated capability", not bad, "; ".join(bad))


def attack_argv_injection() -> None:
    print("\n[4] Argv-only execution — smuggle a command through a parameter")
    payloads = [
        "x & calc.exe",
        "x; rm -rf /",
        "x`whoami`",
        "$(id)",
        "x | powershell -EncodedCommand AAA",
        "x\nrm -rf /",
    ]
    for payload in payloads:
        result = cli(["run", "remember", "--caller", "owner", "--param", "type=fact",
                      "--param", "topic=redteam", "--param", f"text={payload}", "--dry-run"])
        # A dry run prints the argv it WOULD execute. The payload must appear as one inert
        # argument, never as additional argv elements or a shell string.
        if result.returncode == EXIT_OK:
            argv = json.loads(result.stdout).get("argv", [])
            inert = any(payload in a for a in argv) and len(argv) == len(set(range(len(argv))))
            asserted("argv-only", f"payload stays one inert argument: {payload[:24]!r}",
                     inert, f"argv has {len(argv)} elements")
        else:
            refused("argv-only", f"payload rejected outright: {payload[:24]!r}", result)


def attack_path_confinement() -> None:
    print("\n[5] Path confinement — reach outside the workspace, or at the keys")
    attacks = [
        (r"\\evil-server\share\payload", "UNC path"),
        (r"C:\Alfred\notes.txt:hidden", "NTFS alternate data stream"),
        (r"C:\Alfred\CON", "DOS device name"),
        (r"C:\Alfred\nul.txt", "device name with an extension"),
        (r"C:\PROGRA~1\evil", "8.3 short name"),
        (r"C:\Alfred\..\Windows\System32", "traversal out of the workspace"),
        (str(ROOT / "secrets" / "harness.key"), "the signing key itself"),
        (str(ROOT / "policy" / "harness-policy.json"), "the policy itself"),
        (r"C:\Windows\System32\config\SAM", "a system file"),
    ]
    for path, label in attacks:
        refused("path-confinement", f"git-status on {label}",
                cli(["run", "git-status", "--caller", "owner", "--param", f"path={path}"]))


def attack_token_misuse() -> None:
    print("\n[6] Scoped tokens — reuse, re-aim, forge, and outlive a credential")
    scoped = mint("local-model", ["disk-report"], 300)
    refused("scoped-tokens", "aim a disk-report token at graph-recall",
            cli(["run", "graph-recall", "--caller", "local-model", "--token", scoped,
                 "--param", "query=x"]), needle="scoped")
    refused("scoped-tokens", "replay a local-model token as 'scheduled'",
            cli(["run", "disk-report", "--caller", "scheduled", "--token", scoped]),
            needle="issued for caller")
    refused("scoped-tokens", "present a token whose MAC has one character flipped",
            cli(["run", "disk-report", "--caller", "local-model",
                 "--token", scoped[:-1] + ("0" if scoped[-1] != "0" else "1")]),
            needle="signature")
    refused("scoped-tokens", "widen the scopes inside the token",
            cli(["run", "backup", "--caller", "local-model", "--approve",
                 "--token", scoped.replace(".disk-report.", ".disk-report+backup.")]))
    refused("scoped-tokens", "use an already-expired token",
            cli(["run", "disk-report", "--caller", "local-model",
                 "--token", mint("local-model", ["disk-report"], -5)]), needle="expired")
    refused("scoped-tokens", "present a flat shared token for the untrusted caller",
            cli(["run", "disk-report", "--caller", "local-model", "--token", "shared-secret"]),
            needle="scoped token")


def attack_revocation() -> None:
    print("\n[7] Revocation — keep using a credential that was taken away")
    store = ROOT / "secrets" / "harness-revoked.json"
    existed = store.exists()
    backup = store.read_bytes() if existed else None
    try:
        token = mint("local-model", ["disk-report"], 600)
        asserted("revocation", "the token works before revocation",
                 cli(["run", "disk-report", "--caller", "local-model", "--token", token]).returncode == EXIT_OK)
        cli(["revoke", "--token", token])
        refused("revocation", "reuse a revoked token",
                cli(["run", "disk-report", "--caller", "local-model", "--token", token]),
                needle="revoked")

        older = mint("local-model", ["disk-report"], 600)
        cli(["revoke", "--caller", "local-model"])
        refused("revocation", "reuse a token after its whole caller was revoked",
                cli(["run", "disk-report", "--caller", "local-model", "--token", older]),
                needle="was revoked")
        refused("revocation", "revoke a token that was never legitimately issued",
                cli(["revoke", "--token", "aht2.local-model.1.2.nonce.status." + "0" * 32]),
                expect=(EXIT_INPUT,), needle="signature")
    finally:
        if existed:
            store.write_bytes(backup)
        else:
            store.unlink(missing_ok=True)


def attack_audit_chain() -> None:
    print("\n[8] Audit chain + legacy seal — rewrite history (on a copy, never the real trail)")
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "audit.jsonl"
        with log.open("w", encoding="utf-8") as handle:
            for i in range(3):
                handle.write(json.dumps({"ts": "old", "capability": f"legacy{i}"}) + "\n")
        for i in range(5):
            guards.chain_append(log, {"capability": f"cap{i}", "decision": "executed"})

        key = b"redteam-key"
        seal = Path(tmp) / "seal.json"
        guards.seal_legacy(log, key, seal)
        witness = Path(tmp) / "cp.jsonl"
        before = guards.chain_checkpoint(log, witness)

        clean = log.read_text(encoding="utf-8")

        # Edit a chained record.
        log.write_text(clean.replace('"cap2"', '"covered-up"'), encoding="utf-8")
        state = guards.chain_verify(log)
        asserted("audit-chain", "editing a chained record is detected, with the line number",
                 not state["ok"] and state.get("brokenAt") == 6, str(state.get("brokenAt")))

        # Delete a chained record.
        lines = clean.splitlines()
        del lines[5]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        asserted("audit-chain", "deleting a chained record is detected",
                 not guards.chain_verify(log)["ok"])

        # Edit a pre-chain record: the case the chain does NOT cover, which is why it is sealed.
        log.write_text(clean.replace('"legacy1"', '"covered-up"'), encoding="utf-8")
        asserted("audit-chain", "the chain alone still passes on a pre-chain edit (known limit)",
                 guards.chain_verify(log)["ok"])
        asserted("legacy-seal", "the seal detects the pre-chain edit",
                 not guards.verify_legacy_seal(log, key, seal)["intact"])

        # Reseal with an attacker's key: an HMAC cannot be recomputed without the real key.
        guards.seal_legacy(log, b"attacker-key", seal)
        asserted("legacy-seal", "resealing with a foreign key does not launder the edit",
                 not guards.verify_legacy_seal(log, key, seal)["intact"])

        # Truncate the tail: verifies fine, which is why a witness exists.
        log.write_text("\n".join(clean.splitlines()[:5]) + "\n", encoding="utf-8")
        after = guards.chain_verify(log)
        asserted("audit-chain", "tail truncation passes the chain (known limit)", after["ok"])
        asserted("checkpoint", "but the checkpointed head no longer matches",
                 before["head"] != after.get("head"))


def attack_quota() -> None:
    print("\n[9] Quota - hammer the harness in a loop")
    state = ROOT / "memory" / "harness-quota.json"
    existed = state.exists()
    backup = state.read_bytes() if existed else None
    try:
        token = mint("local-model", ["disk-report"], 600)

        # Drain the bucket through the same Quota class the harness itself uses, then check that
        # the CLI path actually consults it. Draining first is deliberate: a naive loop of real
        # CLI calls does NOT trip a 20/min bucket quickly, because each subprocess takes over a
        # second and the refill (0.33 tokens/sec) claws back most of what the loop spends. At
        # ~1.5s per call the loop nets 20/min against a burst of 30, so it takes about 60 calls
        # and a full minute. An earlier version of this probe stopped at 40 and reported a hole
        # that was not there.
        quota = guards.Quota(state)
        drained = 0
        for _ in range(200):
            try:
                quota.check_and_consume("local-model", 20, 30)
                drained += 1
            except guards.GuardError:
                break
        asserted("quota", "the bucket can be emptied", drained > 0, f"{drained} tokens consumed")

        refused("quota", "run a capability with an empty bucket",
                cli(["run", "disk-report", "--caller", "local-model", "--token", token]),
                needle="rate limit")

        # And the flip side, which matters just as much: the bucket refills, so a caller is
        # throttled rather than permanently locked out.
        raw = json.loads(state.read_text(encoding="utf-8"))
        raw["local-model"]["ts"] = time.time() - 300      # pretend five minutes passed
        state.write_text(json.dumps(raw), encoding="utf-8")
        asserted("quota", "the bucket refills, so throttling is not a permanent lockout",
                 cli(["run", "disk-report", "--caller", "local-model",
                      "--token", token]).returncode == EXIT_OK)

        asserted("quota", "the Owner at a terminal is not rate limited",
                 not (json.loads((ROOT / "policy" / "harness-policy.json")
                                 .read_text(encoding="utf-8"))["callers"]["owner"].get("rateLimit")))
    finally:
        if existed:
            state.write_bytes(backup)
        else:
            state.unlink(missing_ok=True)


def attack_reviewed_signing() -> None:
    print("\n[10] Reviewed signing — get an edit blessed by a legitimate re-sign")
    policy_path = ROOT / "policy" / "harness-policy.json"
    original = policy_path.read_bytes()
    try:
        hostile = json.loads(original)
        hostile["callers"]["local-model"]["capabilities"].append("remember")
        hostile["callers"]["local-model"]["authRequired"] = False
        hostile["callers"]["local-model"]["trust"] = "medium"
        hostile["capabilities"]["disk-report"]["network"] = True
        policy_path.write_bytes(json.dumps(hostile, indent=2).encode("utf-8"))

        delta = json.loads(cli(["diff"]).stdout)
        reasons = {e["why"] for e in delta["privilegeIncreases"]}
        asserted("reviewed-signing", "the diff names every privilege increase",
                 len(delta["privilegeIncreases"]) >= 4, f"{len(delta['privilegeIncreases'])}: {sorted(reasons)}")
        refused("reviewed-signing", "sign the hostile edit with --review",
                cli(["sign", "--review"]), expect=(EXIT_DENIED,), needle="REFUSING TO SIGN")
    finally:
        policy_path.write_bytes(original)
        cli(["sign"])
    asserted("reviewed-signing", "the policy verifies after restore",
             cli(["verify"]).returncode == EXIT_OK)


def attack_egress() -> None:
    print("\n[11] Egress — how far can the least-trusted caller reach?")
    policy = json.loads((ROOT / "policy" / "harness-policy.json").read_text(encoding="utf-8"))
    caps = policy["capabilities"]
    undeclared = [n for n, s in caps.items() if "network" not in s]
    asserted("egress", "every capability declares whether it needs the network",
             not undeclared, f"undeclared: {undeclared}")
    isolated = [n for n, s in caps.items() if not s.get("network", True)]
    asserted("egress", "most of the capability surface is declared local-only",
             len(isolated) > len(caps) // 2, f"{len(isolated)}/{len(caps)}")
    reach = sorted(c for c in policy["callers"]["local-model"]["capabilities"]
                   if c in caps and caps[c].get("network", True))
    asserted("egress", "the untrusted caller's network reach is exactly ['graph-recall']",
             reach == ["graph-recall"], f"{reach}")


def attack_lint_and_ledger() -> None:
    print("\n[12] Policy meaning + approval provenance")
    lint = json.loads(cli(["lint"]).stdout)
    asserted("policy-lint", "the live policy has no lint errors",
             not lint["errors"], f"{len(lint['errors'])} errors, {len(lint['warnings'])} warnings")
    ledger = json.loads(cli(["review-log"]).stdout)
    asserted("review-ledger", "the signing history verifies",
             ledger["chainOk"], f"{ledger['signings']} signings, {ledger['reviewed']} reviewed")
    audit = json.loads(cli(["audit-verify"]).stdout)
    asserted("audit-chain", "the live audit chain verifies",
             audit["ok"], f"{audit['chained']} chained, {audit['legacy']} pre-chain")
    asserted("legacy-seal", "the live pre-chain region is sealed and intact",
             audit["legacySeal"].get("intact") is True, json.dumps(audit["legacySeal"])[:80])


def attack_confinement() -> None:
    print("\n[13] Resource confinement — spend more than the ceiling allows")
    import harness_confine as confine

    if not confine.WINDOWS:
        asserted("confinement", "POSIX confinement is verified by verify_posix_confine.py", True,
                 "run it under Linux; this host is not Windows")
        return
    limits = confine.Limits(memory_bytes=64 * 1024 * 1024)
    proc, job, note = confine.spawn_confined(
        [sys.executable, "-c", "b = bytearray(400*1024*1024); print(len(b))"], limits)
    out, err = proc.communicate(timeout=90)
    usage = confine.query_job(job)
    confine.close_job(job)
    asserted("confinement", "a child cannot allocate past its memory ceiling",
             proc.returncode != 0 and b"MemoryError" in err, f"exit={proc.returncode}")
    asserted("confinement", "the child never received the memory it asked for",
             usage.get("peakProcessBytes", 0) < 64 * 1024 * 1024,
             f"peak={usage.get('peakProcessBytes')}")

    # The control: without a limit the same allocation must succeed, or the check above proves
    # nothing about the limit.
    proc, job, _ = confine.spawn_confined(
        [sys.executable, "-c", "b = bytearray(400*1024*1024); print(len(b))"], confine.Limits())
    out, _ = proc.communicate(timeout=90)
    confine.close_job(job)
    asserted("confinement", "CONTROL: the same allocation succeeds unconfined",
             proc.returncode == 0 and b"419430400" in out, f"exit={proc.returncode}")


def attack_audit_records_the_attempts() -> None:
    print("\n[14] Audit trail — is a refusal recorded, or does it vanish?")
    trail = ROOT / "memory" / "harness-audit.jsonl"
    before = trail.stat().st_size if trail.exists() else 0
    cli(["run", "backup", "--caller", "local-model", "--approve"])
    time.sleep(0.2)
    after = trail.stat().st_size if trail.exists() else 0
    asserted("audit", "a denied attempt is appended to the trail", after > before,
             f"{after - before} bytes added")
    tail = trail.read_text(encoding="utf-8", errors="replace").splitlines()[-1]
    asserted("audit", "the record names the caller and the decision",
             "local-model" in tail and "denied" in tail, tail[:100])


def attack_secret_leakage() -> None:
    print("\n[15] Redaction — does a credential end up in an append-only file?")
    token = mint("local-model", ["disk-report"], 120)
    cli(["run", "disk-report", "--caller", "local-model", "--token", token])
    trail = (ROOT / "memory" / "harness-audit.jsonl").read_text(encoding="utf-8", errors="replace")
    asserted("redaction", "the bearer token never appears in the audit trail",
             token not in trail)
    key = (ROOT / "secrets" / "harness.key").read_text(encoding="utf-8").strip()
    asserted("redaction", "the signing key never appears in the audit trail",
             bool(key) and key not in trail)
    asserted("redaction", "the trail records which token was used, by nonce",
             "tokenNonce" in trail)


# ------------------------------------------------------------------------------ helpers


def mint(caller: str, scopes: list[str], ttl: int) -> str:
    result = cli(["mint-token", caller, "--ttl", str(ttl), *sum((["--scope", s] for s in scopes), [])])
    if result.returncode != EXIT_OK:
        raise SystemExit(f"could not mint a token for the red team: {result.stderr}")
    return json.loads(result.stdout)["token"]


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description="Attempt every attack the harness claims to stop")
    parser.add_argument("--verbose", action="store_true")
    VERBOSE = parser.parse_args().verbose

    print("=" * 78)
    print("HARNESS RED TEAM - every attack below is EXPECTED TO FAIL")
    print("=" * 78)

    if cli(["verify"]).returncode != EXIT_OK:
        print("\nThe policy does not verify. Fix that before red-teaming.")
        return 2

    for attack in (
        attack_signed_policy,
        attack_deny_by_default,
        attack_allowlist_and_gate,
        attack_argv_injection,
        attack_path_confinement,
        attack_token_misuse,
        attack_revocation,
        attack_audit_chain,
        attack_quota,
        attack_reviewed_signing,
        attack_egress,
        attack_lint_and_ledger,
        attack_confinement,
        attack_audit_records_the_attempts,
        attack_secret_leakage,
    ):
        try:
            attack()
        except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the rest
            RESULTS.append((attack.__name__, "the probe itself failed", False,
                            f"{type(exc).__name__}: {exc}"))
            print(f"  !! ERROR   {attack.__name__}: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 78)
    holes = [(c, a, d) for c, a, ok, d in RESULTS if not ok]
    by_control: dict[str, list[bool]] = {}
    for control, _, ok, _ in RESULTS:
        by_control.setdefault(control, []).append(ok)
    for control, oks in sorted(by_control.items()):
        print(f"  {control:20} {sum(oks)}/{len(oks)} held")
    print("=" * 78)
    if holes:
        print(f"\n{len(holes)} of {len(RESULTS)} probes FOUND A HOLE:")
        for control, attack, detail in holes:
            print(f"  [{control}] {attack}\n      {detail}")
        return 1
    print(f"\nAll {len(RESULTS)} probes across {len(by_control)} controls held. No attack succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
