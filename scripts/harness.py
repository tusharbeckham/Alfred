#!/usr/bin/env python3
"""Alfred harness — the single, policy-gated entrypoint for automating this machine.

Design goals, in priority order:
  1. An untrusted local model must NOT be able to use this to harm the machine.
  2. Nothing runs unless the signed policy explicitly allows it for that caller.
  3. No shell string is ever constructed. Commands are argv arrays, shell=False.
  4. Every attempt — allowed or refused — is appended to an audit log.

Security model
--------------
* DENY BY DEFAULT. A capability that is not in the policy cannot run.
* The policy is integrity-protected with an HMAC-SHA256 signature over its exact bytes.
  The key lives in ``secrets/harness.key``, which every Alfred agent is denied read access
  to (``**/secrets/**`` is in every agent's write/read deny list). A local model that can
  edit files in the repo therefore cannot forge a policy that grants itself new powers:
  changing the policy invalidates the signature and the harness refuses to run at all.
* Callers are named roles with a capability allowlist. The ``local-model`` role gets
  read-only diagnostics and is ``authRequired``, so it additionally needs a bearer token it
  has no way to obtain.
* Parameters are validated against declared types before substitution. Paths are resolved
  and confined to the allowed workspace roots. Enums are exact-match.
* ``gated`` capabilities require an explicit ``--approve`` from a high-trust caller, so
  unattended and untrusted callers can never trigger them.

Usage
-----
    python scripts/harness.py list [--caller <role>]
    python scripts/harness.py verify
    python scripts/harness.py run <capability> [--caller <role>] [--token <t>]
                                   [--param k=v ...] [--approve] [--dry-run]
    python scripts/harness.py sign          # Owner-only: re-sign after editing the policy

Exit codes: 0 ok · 2 policy/integrity failure · 3 denied · 4 bad input · 5 command failed
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_confine as confine  # noqa: E402 - sibling module, stdlib only
import harness_guards as guards  # noqa: E402 - sibling module, stdlib only

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "harness-policy.json"
SIG_PATH = ROOT / "policy" / "harness-policy.sig"
# A byte-copy of whatever was last signed, so `harness diff` can say what changed. It is
# self-verifying against the signature, so it needs no protection of its own.
SNAPSHOT_PATH = ROOT / "policy" / "harness-policy.signed.json"
KEY_PATH = Path(os.environ.get("ALFRED_HARNESS_KEY_FILE") or (ROOT / "secrets" / "harness.key"))
CALLERS_PATH = ROOT / "secrets" / "harness-callers.json"
# In secrets/ because it is integrity-sensitive, not because it is confidential: a caller
# that could edit this file could UN-revoke a token it had just had taken away.
REVOKED_PATH = ROOT / "secrets" / "harness-revoked.json"
# The legacy-region seal is HMAC'd with the signing key, so it lives beside it: a seal an
# agent could rewrite is a seal that proves nothing.
SEAL_PATH = ROOT / "secrets" / "harness-audit-seal.json"
# The signing history. In secrets/ so it cannot be rewritten by anything in the repo: a record
# of approvals that an agent could edit is a record of whatever that agent preferred.
REVIEW_LEDGER_PATH = ROOT / "secrets" / "harness-review-log.jsonl"
QUOTA_PATH = ROOT / "memory" / "harness-quota.json"
CHECKPOINT_PATH = ROOT / "memory" / "harness-audit-checkpoints.jsonl"

EXIT_OK, EXIT_POLICY, EXIT_DENIED, EXIT_INPUT, EXIT_FAILED = 0, 2, 3, 4, 5


class PolicyError(RuntimeError):
    """The policy is missing, malformed, or its signature does not verify."""


class Denied(RuntimeError):
    """The request is well-formed but not permitted."""


class BadInput(RuntimeError):
    """The caller supplied invalid parameters."""


# --------------------------------------------------------------------------- policy


def read_policy_bytes() -> bytes:
    if not POLICY_PATH.exists():
        raise PolicyError(f"Policy file is missing: {POLICY_PATH}")
    return POLICY_PATH.read_bytes()


def load_key() -> bytes | None:
    """The signing key. Absent key means the harness cannot verify integrity."""
    if not KEY_PATH.exists():
        return None
    key = KEY_PATH.read_bytes().strip()
    return key or None


def canonical_policy_bytes(policy_bytes: bytes) -> bytes:
    """Canonicalize the policy bytes before signing/verifying.

    The signature must survive a git checkout. On Windows with core.autocrlf=true,
    git rewrites LF to CRLF on checkout, which changes the raw bytes and would
    invalidate an HMAC taken over them - bricking the whole harness on a fresh
    clone even though the policy content is authentic and unmodified.

    We therefore normalize line endings (CRLF and lone CR both -> LF) before
    hashing. This is safe: line endings carry no semantic meaning in JSON, so an
    attacker cannot change what the policy *means* via line endings alone. Every
    semantic byte is still covered by the HMAC.

    Deliberately NOT stripping trailing whitespace/newlines: keeping the
    canonical form minimal means signatures generated before this fix still
    verify, so the existing policy is proven authentic without re-signing.
    """
    return policy_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def compute_signature(policy_bytes: bytes, key: bytes) -> str:
    return hmac.new(key, canonical_policy_bytes(policy_bytes), hashlib.sha256).hexdigest()


def verify_policy(*, require_signature: bool = True) -> dict[str, Any]:
    """Parse the policy and verify its HMAC signature. Fails closed."""
    raw = read_policy_bytes()
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"Policy is not valid JSON: {exc}") from exc

    settings = policy.get("settings", {})
    needs_sig = settings.get("requireSignature", True) and require_signature
    if not needs_sig:
        return policy

    key = load_key()
    if key is None:
        raise PolicyError(
            f"No signing key at {KEY_PATH}. Run 'python scripts/harness.py sign' as the Owner "
            "to create one, or set requireSignature=false in the policy (not recommended)."
        )
    if not SIG_PATH.exists():
        raise PolicyError(f"Policy signature is missing: {SIG_PATH}. Re-sign the policy.")

    expected = compute_signature(raw, key)
    actual = SIG_PATH.read_text(encoding="utf-8").strip()
    if not hmac.compare_digest(expected, actual):
        raise PolicyError(
            "POLICY INTEGRITY FAILURE — harness-policy.json does not match its signature. "
            "The policy was modified without the signing key. Refusing to run anything."
        )
    return policy


def sign_policy() -> str:
    key = load_key()
    if key is None:
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(os.urandom(48)).hexdigest().encode("ascii")
        KEY_PATH.write_bytes(key + b"\n")
        try:
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass
    raw = read_policy_bytes()
    signature = compute_signature(raw, key)
    SIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIG_PATH.write_text(signature + "\n", encoding="utf-8")
    # Snapshot exactly what was signed. Without this there is no way to answer "what
    # changed since the last signature?", because a signature is a fingerprint of content
    # rather than a copy of it. The snapshot needs no protection of its own: it is
    # self-verifying, because a snapshot whose HMAC matches the .sig *is* the signed
    # content, and one that does not is discarded by `signed_policy()`.
    SNAPSHOT_PATH.write_bytes(raw)
    return signature


def signed_policy() -> dict[str, Any] | None:
    """The policy as it was when last signed, or None if that cannot be established.

    The snapshot is trusted only when its own HMAC matches the current signature. That
    makes it impossible to fool the diff by editing the snapshot: an edited snapshot no
    longer matches the signature, so it is refused rather than believed.
    """
    if not SNAPSHOT_PATH.exists():
        return None
    key = load_key()
    if key is None or not SIG_PATH.exists():
        return None
    raw = SNAPSHOT_PATH.read_bytes()
    expected = compute_signature(raw, key)
    if not hmac.compare_digest(expected, SIG_PATH.read_text(encoding="utf-8").strip()):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------- auth


def authenticate(caller: str, token: str | None, spec: dict[str, Any],
                 capability: str | None = None) -> dict[str, Any] | None:
    """Authenticate a caller. Returns token claims for a scoped token, else None.

    Two token formats are accepted, deliberately:

    * **Scoped tokens** (``aht1.…``, minted by ``harness mint-token``) carry the
      caller, an expiry and the exact capabilities they cover *inside* the HMAC.
      This is the format that answers the confused-deputy problem: a token issued
      so the local model can read the memory graph cannot be turned against
      ``graph-assert`` by a prompt injection, because the capability name is signed.
    * **Flat tokens** in ``secrets/harness-callers.json`` (a SHA-256 of the shared
      secret). Kept because revoking every provisioned caller in order to ship an
      improvement is a worse outcome than supporting two formats — but they never
      expire and are not scoped, so a policy can require scoped tokens per caller
      via ``requireScopedToken``.
    """
    if not spec.get("authRequired", False):
        return None
    if not token:
        raise Denied(f"Caller '{caller}' requires a token (--token or ALFRED_HARNESS_TOKEN).")

    if token.startswith(guards.TOKEN_VERSION + "."):
        key = load_key()
        if key is None:
            raise Denied("Scoped tokens cannot be verified without the harness signing key.")
        try:
            return guards.verify_token(
                key, token, caller, capability or "*",
                revocations=guards.load_revocations(REVOKED_PATH),
            )
        except guards.GuardError as exc:
            raise Denied(f"Token rejected: {exc}") from exc

    if spec.get("requireScopedToken", False):
        raise Denied(
            f"Caller '{caller}' must present a scoped token (harness mint-token); "
            "a flat shared token is not accepted for this caller."
        )

    if not CALLERS_PATH.exists():
        raise Denied(
            f"Caller '{caller}' requires a token but no token store exists at {CALLERS_PATH}. "
            "The Owner must provision one before this caller can be used."
        )
    try:
        store = json.loads(CALLERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Denied(f"Caller token store is unreadable: {exc}") from exc
    expected = store.get(caller)
    if not expected:
        raise Denied(f"No token is provisioned for caller '{caller}'.")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(expected), digest):
        raise Denied(f"Invalid token for caller '{caller}'.")
    return None


# ---------------------------------------------------------------- param validation


def _inside(path: Path, roots: list[str]) -> bool:
    """Kept as a thin alias so existing callers/tests keep working."""
    return guards.inside_roots(path, roots)


def validate_params(
    capability: str, declared: dict[str, Any], supplied: dict[str, str], policy: dict[str, Any]
) -> dict[str, str]:
    roots = policy.get("settings", {}).get("allowedWorkspaceRoots", [str(ROOT)])
    forbidden = policy.get("forbidden", {})
    unknown = set(supplied) - set(declared)
    if unknown:
        raise BadInput(f"{capability}: unknown parameter(s): {', '.join(sorted(unknown))}")

    clean: dict[str, str] = {}
    for name, rule in declared.items():
        if name not in supplied:
            raise BadInput(f"{capability}: missing required parameter '{name}'")
        value = supplied[name]
        kind = rule.get("type", "string")

        if "\x00" in value or "\n" in value or "\r" in value:
            raise BadInput(f"{capability}.{name}: control characters are not allowed")

        if kind == "enum":
            allowed = rule.get("values", [])
            if value not in allowed:
                raise BadInput(f"{capability}.{name}: must be one of {allowed}")
        elif kind == "path":
            # safe_resolve runs BEFORE the forbidden-pattern checks on purpose. Those
            # checks are regexes over a path string, and every Windows trick the
            # resolver refuses - 8.3 short names, alternate data streams, junctions,
            # device names, UNC prefixes - exists precisely to make a path string look
            # different from the file it opens. Pattern-matching an unnormalized path
            # is pattern-matching the attacker's spelling, not the target.
            try:
                candidate = guards.safe_resolve(value, ROOT)
            except guards.GuardError as exc:
                raise Denied(f"{capability}.{name}: {exc}") from exc
            for pattern in forbidden.get("pathPatterns", []):
                if re.search(pattern, str(candidate).replace("\\", "/")):
                    raise Denied(f"{capability}.{name}: path matches a forbidden pattern")
            normalized = str(candidate).replace("\\", "/")
            for prefix in forbidden.get("pathPrefixes", []):
                if normalized.lower().startswith(prefix.lower()):
                    raise Denied(f"{capability}.{name}: path is inside a forbidden location ({prefix})")
            if rule.get("mustBeInsideWorkspace", True) and not guards.inside_roots(candidate, roots):
                raise Denied(f"{capability}.{name}: path escapes the allowed workspace roots {roots}")
            value = str(candidate)
        else:
            limit = int(rule.get("maxLength", 500))
            if len(value) > limit:
                raise BadInput(f"{capability}.{name}: longer than {limit} characters")
        clean[name] = value
    return clean


def build_argv(spec: dict[str, Any], params: dict[str, str]) -> list[str]:
    """Substitute {placeholders} into the argv array. No shell, no concatenation."""
    argv = [spec["command"]]
    for arg in spec.get("args", []):
        rendered = arg
        for name, value in params.items():
            rendered = rendered.replace("{" + name + "}", value)
        if re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", rendered):
            raise BadInput(f"Unresolved placeholder in argument: {rendered}")
        argv.append(rendered)
    return argv


def scan_forbidden(argv: list[str], policy: dict[str, Any]) -> None:
    """Defence in depth: refuse dangerous content even in an allowlisted capability."""
    patterns = policy.get("forbidden", {}).get("argumentPatterns", [])
    joined = " ".join(argv)
    for pattern in patterns:
        if re.search(pattern, joined):
            raise Denied(f"Refused: argument matches forbidden pattern /{pattern}/")


# --------------------------------------------------------------------------- audit


def audit_path(policy: dict[str, Any]) -> Path:
    rel = policy.get("settings", {}).get("auditLog", "memory/harness-audit.jsonl")
    return ROOT / rel


def audit(policy: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Append one tamper-evident, redacted record to the audit trail.

    Two changes from a plain append here, both about trusting the trail later:

    * **Redaction.** Parameter *values* are filtered through an allowlist before
      they are written. The trail is append-only, so a secret logged once is logged
      permanently; a denylist of secret-shaped regexes would have to be right the
      first time, every time, for every parameter added in future.
    * **Hash chaining.** Each record carries the hash of the one before it, so any
      later edit or deletion inside the file is detectable by ``harness audit-verify``.
    """
    settings = policy.get("settings", {})
    loggable = settings.get("auditLoggableParams", guards.DEFAULT_LOGGABLE_PARAMS)
    body = dict(record)
    if "params" in body and isinstance(body["params"], dict):
        body["params"] = guards.redact(body["params"], loggable)
    body = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **body}
    if settings.get("auditChain", True):
        return guards.chain_append(audit_path(policy), body)
    path = audit_path(policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(body, ensure_ascii=False) + "\n")
    return body


# ----------------------------------------------------------------------- execution


@dataclass
class Result:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    argv: list[str]
    truncated: bool = False
    confinement: str = "none"
    usage: dict[str, Any] = field(default_factory=dict)


def execute(argv: list[str], timeout: int, max_output: int = 4 * 1024 * 1024,
            limits: "confine.Limits | None" = None,
            isolate_network: bool = False) -> Result:
    """Run an argv array with a wall-clock timeout, an output cap, and a resource ceiling.

    Three bounds, because a child can exhaust the machine three different ways:

    * **time** — kill a child that never finishes.
    * **output** — ``capture_output=True`` buffers the child's entire output in the
      parent's memory, so "write a lot to stdout" was a denial of service against the
      harness itself, available to any caller permitted to run any capability at all —
      including the untrusted local model, whose read-only diagnostics produce output
      sized by the state of the machine rather than by the policy.
    * **memory, CPU and process count** — a Windows Job Object (``harness_confine``).
      This is the bound ``subprocess`` cannot express at all, and the only one that also
      covers the child's *descendants*: killing a child on timeout leaves its
      grandchildren running, unparented and outside the audit trail, whereas the job
      terminates the whole tree — even if the harness itself dies unexpectedly.
    """
    job = None
    confinement = "none"
    try:
        proc, job, confinement = confine.spawn_confined(
            argv, limits or confine.Limits(), cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            isolate_network=isolate_network,
        )
    except FileNotFoundError:
        return Result(False, 127, "", f"Executable not found: {argv[0]}", argv)
    except confine.ConfinementError as exc:
        # Confinement was configured and could not be established. The child was killed
        # while still suspended, so nothing ran. Refusing is the right direction: quietly
        # running unconfined turns a control someone configured into a suggestion.
        return Result(False, 125, "", f"Refusing to run unconfined: {exc}", argv,
                      confinement="failed")
    except OSError as exc:
        return Result(False, 126, "", f"Could not start {argv[0]}: {exc}", argv)

    # stderr gets a smaller share: a failing command's useful signal is near the start,
    # and a chatty stderr must not be able to crowd out the actual result.
    err_cap = max(65536, max_output // 8)
    deadline = time.monotonic() + timeout
    try:
        out_text, out_cut = guards.bounded_read(proc.stdout, max_output)
        err_text, err_cut = guards.bounded_read(proc.stderr, err_cap)
        remaining = max(1.0, deadline - time.monotonic())
        code = proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        usage = confine.query_job(job)
        confine.close_job(job)
        return Result(False, 124, "", f"Timed out after {timeout}s", argv,
                      confinement=confinement, usage=usage)
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    # Read the accounting BEFORE closing the handle: closing destroys the job, and with
    # it the peak-memory numbers that make the limit auditable rather than merely claimed.
    usage = confine.query_job(job)
    confine.close_job(job)

    truncated = out_cut or err_cut
    if truncated:
        out_text += f"\n[harness] output truncated at {max_output} bytes\n"
    return Result(code == 0, code, out_text, err_text, argv, truncated, confinement, usage)


def resolve_caller(policy: dict[str, Any], caller: str) -> dict[str, Any]:
    spec = policy.get("callers", {}).get(caller)
    if spec is None:
        raise Denied(f"Unknown caller role '{caller}'. Known: {sorted(policy.get('callers', {}))}")
    return spec


def allowed_capabilities(policy: dict[str, Any], caller_spec: dict[str, Any]) -> list[str]:
    granted = caller_spec.get("capabilities", [])
    everything = sorted(policy.get("capabilities", {}))
    return everything if "*" in granted else [c for c in everything if c in granted]


def run_capability(
    policy: dict[str, Any],
    capability: str,
    caller: str,
    token: str | None,
    raw_params: dict[str, str],
    approve: bool,
    dry_run: bool,
    observer=None,
) -> Result:
    """Run a capability through every policy control, in order.

    ``observer`` is an optional callback ``(stage, ok, detail)`` fired as each
    control passes. Every stage below is a REAL check that can refuse the call -
    nothing is emitted for decoration, so a UI rendering these is showing the
    actual policy chain rather than a progress animation.
    """
    def stage(name: str, ok: bool = True, detail: str = "") -> None:
        if observer is not None:
            try:
                observer(name, ok, detail)
            except Exception:  # noqa: BLE001 - a display must never break the policy
                pass

    try:
        caller_spec = resolve_caller(policy, caller)
    except Denied:
        stage("caller", False, f"unknown caller '{caller}'")
        raise
    stage("caller", True, f"{caller} (trust={caller_spec.get('trust')})")

    try:
        claims = authenticate(caller, token, caller_spec, capability)
    except Denied as exc:
        stage("auth", False, str(exc)[:80])
        raise
    if claims:
        stage("auth", True, f"scoped token, {claims['expiresIn']}s left, scopes={claims['scopes']}")
    else:
        stage("auth", True, "token required" if caller_spec.get("authRequired") else "not required")

    spec = policy.get("capabilities", {}).get(capability)
    if spec is None:
        stage("defined", False, "not in policy (deny by default)")
        raise Denied(f"Capability '{capability}' is not defined in the policy (deny by default).")
    stage("defined", True, f"risk={spec.get('risk')}")

    if capability not in allowed_capabilities(policy, caller_spec):
        stage("allowlist", False, f"'{caller}' may not run '{capability}'")
        raise Denied(f"Caller '{caller}' is not permitted to run '{capability}'.")
    stage("allowlist", True, "permitted for this caller")

    # Rate limit. A model in a loop is not a hostile model, but it consumes the same
    # machine, and a bounded caller cannot turn a bug into an outage. Deliberately
    # placed AFTER the allowlist so a denied capability does not spend quota - being
    # refused should not cost a caller its budget - and BEFORE the gate so an
    # approval prompt cannot be used to hammer the harness for free.
    limits = caller_spec.get("rateLimit") or {}
    per_minute = int(limits.get("perMinute", 0))
    if per_minute > 0 and not dry_run:
        try:
            usage = guards.Quota(QUOTA_PATH).check_and_consume(
                caller, per_minute, limits.get("burst")
            )
        except guards.GuardError as exc:
            stage("quota", False, str(exc)[:90])
            raise Denied(str(exc)) from exc
        stage("quota", True, f"{usage['remaining']}/{usage['capacity']} left")
    else:
        stage("quota", True, "no limit for this caller")

    if spec.get("gated", False):
        if caller_spec.get("trust") != "high":
            stage("gate", False, f"gated; trust={caller_spec.get('trust')} is too low")
            raise Denied(
                f"'{capability}' is a gated capability and requires a high-trust caller; "
                f"'{caller}' is trust={caller_spec.get('trust')}."
            )
        if not approve:
            stage("gate", False, "gated; needs explicit --approve")
            raise Denied(f"'{capability}' is gated. Re-run with --approve to confirm.")
        stage("gate", True, "gated, approved by the Owner")
    else:
        stage("gate", True, "ungated")

    try:
        params = validate_params(capability, spec.get("params", {}), raw_params, policy)
    except (BadInput, Denied) as exc:
        stage("params", False, str(exc)[:80])
        raise
    stage("params", True, f"{len(params)} validated" if params else "none required")

    try:
        argv = build_argv(spec, params)
        scan_forbidden(argv, policy)
    except (BadInput, Denied) as exc:
        stage("argv", False, str(exc)[:80])
        raise
    stage("argv", True, f"{len(argv)} args, no shell")

    timeout = int(policy.get("settings", {}).get("maxRuntimeSeconds", 900))
    max_output = int(policy.get("settings", {}).get("maxOutputBytes", 4 * 1024 * 1024))
    limits = confine.limits_from_policy(policy.get("settings", {}), caller_spec.get("trust"))
    base = {
        "caller": caller,
        "trust": caller_spec.get("trust"),
        "capability": capability,
        "risk": spec.get("risk"),
        "argv": argv,
        "params": params,
        "gated": bool(spec.get("gated", False)),
        "approved": bool(approve),
    }
    if claims:
        # The nonce, never the token. An audit trail that records bearer tokens is a
        # credential store with a friendly name.
        base["tokenNonce"] = claims["nonce"]

    if dry_run:
        audit(policy, {**base, "decision": "dry-run"})
        stage("execute", True, "dry-run: nothing executed")
        stage("audit", True, "appended")
        return Result(True, 0, json.dumps({"dryRun": True, "argv": argv}, indent=2), "", argv)

    if limits.any_set():
        stage("confine", True,
              f"mem={_mib(limits.memory_bytes)} job={_mib(limits.job_memory_bytes)} "
              f"procs={limits.active_processes or '-'} cpu={limits.cpu_seconds or '-'}s")
    else:
        stage("confine", True, "no resource limits for this trust level")

    # Egress. `network` is a per-capability declaration: false means the capability has no
    # business reaching the network, and on Linux that is enforced with a fresh network
    # namespace. On Windows it is declaration and audit only - there is no equivalent short of
    # AppContainer - so the stage reports which of the two happened rather than implying the
    # stronger one everywhere.
    needs_network = bool(spec.get("network", True))
    isolate_network = not needs_network
    if not isolate_network:
        stage("egress", True, "capability declares it needs the network")
    elif confine.network_isolation_available():
        stage("egress", True, "isolated: no network namespace interfaces")
    else:
        stage("egress", True, "declared local-only; no enforcement on this platform")

    stage("execute", True, "running")
    result = execute(argv, timeout, max_output, limits, isolate_network)
    stage("execute", result.ok, f"exit {result.exit_code}")
    audit(policy, {**base, "decision": "executed", "exitCode": result.exit_code,
                   "ok": result.ok, "truncated": result.truncated,
                   "confinement": result.confinement,
                   "network": "needed" if needs_network else "isolated",
                   "peakBytes": result.usage.get("peakJobBytes"),
                   "processes": result.usage.get("totalProcesses"),
                   "cpuSeconds": result.usage.get("cpuSeconds"),
                   # Disk is accounted, not bounded, on Windows. Logging it is what makes
                   # "this capability wrote 4 GB" a question someone can ask afterwards.
                   "bytesWritten": result.usage.get("bytesWritten")})
    stage("audit", True, "appended to the trail")
    return result


def _mib(value: int | None) -> str:
    """Render a byte limit for the control chain display."""
    return f"{value // (1024 * 1024)}MiB" if value else "-"


# ------------------------------------------------------------------------------ cli


def _write(stream, text: str) -> None:
    """Write text to a console that may not be able to represent it.

    A capability's output is whatever its child produced, decoded with ``errors="replace"``,
    so it can legitimately contain U+FFFD — and a cp1252 console raises
    ``UnicodeEncodeError`` on that. The harness was therefore able to run a capability
    successfully and then die with a traceback while *printing* the result, which reads
    like the capability failed. Encoding through the stream's own codec with replacement
    means the worst case is an unrepresentable character shown as '?', not a lost result.
    """
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text)
    except UnicodeEncodeError:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode(encoding, errors="replace"))
            buffer.flush()
        else:
            stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def parse_params(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise BadInput(f"--param expects key=value, got '{pair}'")
        key, value = pair.split("=", 1)
        out[key.strip()] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="Alfred policy-gated automation harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List capabilities available to a caller")
    p_list.add_argument("--caller", default="owner")
    p_list.add_argument("--json", action="store_true")

    sub.add_parser("verify", help="Verify the policy signature and report the policy summary")

    p_sign = sub.add_parser("sign", help="Owner-only: (re)generate the policy signature")
    p_sign.add_argument("--review", action="store_true",
                        help="Show what changed since the last signature and refuse privilege increases without --accept-escalation")
    p_sign.add_argument("--accept-escalation", action="store_true",
                        help="With --review: proceed even though the change grants new privilege")

    sub.add_parser("diff", help="What changed in the policy since it was last signed?")
    sub.add_parser("review-log", help="The signing history: what was reviewed, and what was not")

    p_lint = sub.add_parser("lint", help="Static checks on the policy's meaning (not just its signature)")
    p_lint.add_argument("--strict", action="store_true", help="Treat warnings as failures too")

    sub.add_parser("audit-verify", help="Verify the audit trail's hash chain is unbroken")
    sub.add_parser("seal-legacy", help="Owner-only: seal the pre-chain audit records so later edits are detectable")
    sub.add_parser("checkpoint", help="Record the current audit chain head as an external witness")

    p_mint = sub.add_parser("mint-token", help="Owner-only: mint a scoped, expiring caller token")
    p_mint.add_argument("caller")
    p_mint.add_argument("--scope", action="append", default=[],
                        help="Capability this token may be used for (repeatable). Omit for all.")
    p_mint.add_argument("--ttl", type=int, default=3600, help="Lifetime in seconds (default 3600)")

    p_quota = sub.add_parser("quota", help="Show the current rate-limit budget per caller")
    p_quota.add_argument("--caller")

    p_revoke = sub.add_parser("revoke", help="Owner-only: revoke a scoped token, or every token for a caller")
    p_revoke.add_argument("--token", help="Revoke exactly this token")
    p_revoke.add_argument("--caller", help="Revoke EVERY token already issued for this caller")

    sub.add_parser("revocations", help="Show what is currently revoked")

    p_run = sub.add_parser("run", help="Run a capability")
    p_run.add_argument("capability")
    p_run.add_argument("--caller", default=os.environ.get("ALFRED_HARNESS_CALLER", "owner"))
    p_run.add_argument("--token", default=os.environ.get("ALFRED_HARNESS_TOKEN"))
    p_run.add_argument("--param", action="append", default=[])
    p_run.add_argument("--approve", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    # Branding, but only when a human is watching. `verify` and `list` emit JSON
    # that callers (and the test suite) parse, so a banner on stdout would corrupt
    # it - hence stderr, and only when stdout is a TTY.
    if sys.stdout.isatty() and args.command in ("verify", "list"):
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import brand

            print(f"{brand.CYAN}{brand.BOLD}ALFRED{brand.RESET}"
                  f"{brand.DIM} harness | policy-gated automation{brand.RESET}",
                  file=sys.stderr)
        except Exception:  # noqa: BLE001 - branding must never break the harness
            pass

    try:
        if args.command == "sign":
            review: dict[str, Any] | None = None
            if args.review:
                # Signing is the one point in the whole design where a human blesses a
                # change, and it was a rubber stamp: `sign` hashed whatever bytes were on
                # disk and reported nothing about them. A model that edits the policy
                # cannot forge a signature — but it could wait for the Owner to re-sign for
                # an unrelated reason and have its edit blessed for free.
                previous = signed_policy()
                if previous is None:
                    review = {
                        "review": "unavailable",
                        "reason": "no verifiable snapshot of the last signed policy; "
                                  "signing now establishes the baseline for future reviews",
                    }
                else:
                    try:
                        current = json.loads(read_policy_bytes())
                    except json.JSONDecodeError as exc:
                        print(f"POLICY ERROR: current policy is not valid JSON: {exc}",
                              file=sys.stderr)
                        return EXIT_POLICY
                    delta = guards.diff_policy(previous, current)
                    lint_errors = [f for f in guards.lint_policy(current) if f["level"] == "error"]
                    review = {"review": delta, "lintErrors": lint_errors}
                    # Two independent objections. Lint catches a policy that cannot mean
                    # what it says; the diff catches one that means something new. Neither
                    # subsumes the other, so both can refuse.
                    if lint_errors:
                        print(json.dumps(review, indent=2))
                        print("REFUSING TO SIGN: the policy has lint errors. Fix them first.",
                              file=sys.stderr)
                        return EXIT_POLICY
                    if delta["privilegeIncreases"] and not args.accept_escalation:
                        print(json.dumps(review, indent=2))
                        print(f"REFUSING TO SIGN: this change grants new privilege "
                              f"({len(delta['privilegeIncreases'])} finding(s) above). "
                              "Re-run with --accept-escalation if that is intended.",
                              file=sys.stderr)
                        return EXIT_DENIED
            signature = sign_policy()
            # Every signing goes in the ledger, reviewed or not. `sign --review` can say what
            # changed since the last signature; only the ledger can say whether that signature
            # was itself examined — and the first one on any clone never was.
            try:
                delta = (review or {}).get("review")
                guards.record_review(REVIEW_LEDGER_PATH, {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "signature": signature[:16],
                    "reviewed": bool(args.review and isinstance(delta, dict)),
                    "acceptedEscalations": (
                        [e["key"] for e in delta["privilegeIncreases"]]
                        if isinstance(delta, dict) and args.accept_escalation else []
                    ),
                    "changes": (
                        len(delta["added"]) + len(delta["removed"]) + len(delta["changed"])
                        if isinstance(delta, dict) else None
                    ),
                    "policyVersion": json.loads(read_policy_bytes()).get("version"),
                })
            except Exception as exc:  # noqa: BLE001 - a ledger failure must not block signing,
                # but it must be visible, because a provenance record with silent gaps is worse
                # than none.
                print(f"WARNING: could not record the review ({exc})", file=sys.stderr)
            # One JSON document per invocation, which is the contract every other
            # subcommand follows and what callers parse.
            print(json.dumps({**(review or {}), "signed": True, "signature": signature,
                              "key": str(KEY_PATH)}, indent=2))
            return EXIT_OK

        if args.command == "review-log":
            print(json.dumps(guards.review_history(REVIEW_LEDGER_PATH), indent=2))
            return EXIT_OK

        if args.command == "diff":
            previous = signed_policy()
            if previous is None:
                print(json.dumps({
                    "diff": "unavailable",
                    "reason": "no verifiable snapshot of the last signed policy. Either the "
                              "policy has never been signed by this clone, or the snapshot "
                              "no longer matches the signature (in which case it is refused "
                              "rather than trusted).",
                }, indent=2))
                return EXIT_OK
            try:
                current = json.loads(read_policy_bytes())
            except json.JSONDecodeError as exc:
                print(f"POLICY ERROR: current policy is not valid JSON: {exc}", file=sys.stderr)
                return EXIT_POLICY
            delta = guards.diff_policy(previous, current)
            print(json.dumps({"snapshot": str(SNAPSHOT_PATH), **delta}, indent=2))
            return EXIT_OK

        policy = verify_policy()

        if args.command == "lint":
            findings = guards.lint_policy(policy)
            errors = [f for f in findings if f["level"] == "error"]
            warnings = [f for f in findings if f["level"] == "warn"]
            print(json.dumps({
                "policy": str(POLICY_PATH),
                "ok": not errors and (not warnings or not args.strict),
                "errors": errors,
                "warnings": warnings,
            }, indent=2))
            if errors or (args.strict and warnings):
                return EXIT_POLICY
            return EXIT_OK

        if args.command == "audit-verify":
            state = guards.chain_verify(audit_path(policy))
            seal = guards.verify_legacy_seal(audit_path(policy), load_key(), SEAL_PATH)
            print(json.dumps({"auditLog": str(audit_path(policy)), **state,
                              "legacySeal": seal}, indent=2))
            # A broken chain OR an altered pre-chain region both mean the trail cannot be
            # trusted. Only the chain used to fail this command, which left the older
            # records outside the verdict entirely.
            ok = state["ok"] and seal.get("intact", True)
            return EXIT_OK if ok else EXIT_POLICY

        if args.command == "seal-legacy":
            key = load_key()
            if key is None:
                print("POLICY ERROR: no signing key; cannot seal.", file=sys.stderr)
                return EXIT_POLICY
            try:
                payload = guards.seal_legacy(audit_path(policy), key, SEAL_PATH)
            except guards.GuardError as exc:
                print(json.dumps({"sealed": False, "reason": str(exc)}, indent=2))
                return EXIT_OK
            print(json.dumps({"seal": str(SEAL_PATH), **payload}, indent=2))
            return EXIT_OK

        if args.command == "checkpoint":
            try:
                payload = guards.chain_checkpoint(audit_path(policy), CHECKPOINT_PATH)
            except guards.GuardError as exc:
                print(f"POLICY ERROR: {exc}", file=sys.stderr)
                return EXIT_POLICY
            print(json.dumps({"checkpoint": str(CHECKPOINT_PATH), **payload}, indent=2))
            return EXIT_OK

        if args.command == "mint-token":
            caller_spec = resolve_caller(policy, args.caller)
            key = load_key()
            if key is None:
                print("POLICY ERROR: no signing key; run 'harness sign' first.", file=sys.stderr)
                return EXIT_POLICY
            allowed = allowed_capabilities(policy, caller_spec)
            scopes = args.scope or allowed
            # A token must never be able to widen what the policy grants. Minting one
            # for a capability the caller cannot run would create a credential that
            # looks authoritative and is refused later for a different reason - a
            # confusing failure that invites someone to "fix" it by editing the policy.
            outside = sorted(set(scopes) - set(allowed))
            if outside:
                print(f"BAD INPUT: caller '{args.caller}' is not permitted to run {outside}; "
                      "a token cannot grant more than the policy does.", file=sys.stderr)
                return EXIT_INPUT
            token = guards.mint_token(key, args.caller, scopes, args.ttl)
            print(json.dumps({
                "caller": args.caller, "scopes": sorted(scopes), "ttlSeconds": args.ttl,
                "token": token,
                "note": "Store this now; it is not recoverable. It expires on its own.",
            }, indent=2))
            return EXIT_OK

        if args.command == "quota":
            try:
                state = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            report = {}
            for name, spec in policy.get("callers", {}).items():
                if args.caller and name != args.caller:
                    continue
                limits = spec.get("rateLimit") or {}
                per_minute = int(limits.get("perMinute", 0))
                capacity = float(limits.get("burst", per_minute) or 0)
                bucket = state.get(name) or {}
                tokens = float(bucket.get("tokens", capacity))
                if per_minute > 0 and bucket:
                    elapsed = max(0.0, time.time() - float(bucket.get("ts", 0)))
                    tokens = min(capacity, tokens + elapsed * (per_minute / 60.0))
                report[name] = {
                    "perMinute": per_minute or None,
                    "burst": int(capacity) or None,
                    "available": round(tokens, 2) if per_minute > 0 else None,
                }
            print(json.dumps({"quotaState": str(QUOTA_PATH), "callers": report}, indent=2))
            return EXIT_OK

        if args.command == "revocations":
            state = guards.load_revocations(REVOKED_PATH)
            print(json.dumps({
                "store": str(REVOKED_PATH),
                "revokedTokens": len(state["nonces"]),
                "nonces": sorted(state["nonces"]),
                "callerEpochs": state["callerEpochs"],
                "note": "Expired entries are pruned on read; a dead token needs no entry.",
            }, indent=2))
            return EXIT_OK

        if args.command == "revoke":
            if bool(args.token) == bool(args.caller):
                print("BAD INPUT: give exactly one of --token or --caller.", file=sys.stderr)
                return EXIT_INPUT
            if args.caller:
                resolve_caller(policy, args.caller)   # refuse to revoke an unknown role
                state = guards.revoke_caller(REVOKED_PATH, args.caller)
                print(json.dumps({
                    "revoked": "all tokens", "caller": args.caller,
                    "epoch": state["callerEpochs"][args.caller],
                    "note": "Tokens minted from now on are unaffected; their iat is later.",
                }, indent=2))
                return EXIT_OK
            key = load_key()
            if key is None:
                print("POLICY ERROR: no signing key; cannot verify the token to revoke.",
                      file=sys.stderr)
                return EXIT_POLICY
            try:
                claims = guards.token_claims(key, args.token)
            except guards.GuardError as exc:
                # Refusing here matters: accepting an unverified token would let anyone
                # fill the revocation list with invented nonces, which is read on every
                # authenticated call.
                print(f"BAD INPUT: {exc}", file=sys.stderr)
                return EXIT_INPUT
            guards.revoke_nonce(REVOKED_PATH, claims["nonce"], claims["exp"])
            print(json.dumps({
                "revoked": claims["nonce"], "caller": claims["caller"],
                "scopes": claims["scopes"], "expiresAt": claims["exp"],
            }, indent=2))
            return EXIT_OK

        if args.command == "verify":
            print(json.dumps({
                "policy": str(POLICY_PATH),
                "signatureValid": True,
                "denyByDefault": policy.get("settings", {}).get("denyByDefault"),
                "callers": {name: spec.get("trust") for name, spec in policy.get("callers", {}).items()},
                "capabilityCount": len(policy.get("capabilities", {})),
                "gated": sorted(k for k, v in policy.get("capabilities", {}).items() if v.get("gated")),
                "auditChain": policy.get("settings", {}).get("auditChain", True),
                "maxOutputBytes": policy.get("settings", {}).get("maxOutputBytes"),
                "rateLimited": sorted(
                    name for name, spec in policy.get("callers", {}).items()
                    if int((spec.get("rateLimit") or {}).get("perMinute", 0)) > 0
                ),
                "lintErrors": len([f for f in guards.lint_policy(policy) if f["level"] == "error"]),
            }, indent=2))
            return EXIT_OK

        if args.command == "list":
            caller_spec = resolve_caller(policy, args.caller)
            caps = allowed_capabilities(policy, caller_spec)
            detail = {
                "caller": args.caller,
                "trust": caller_spec.get("trust"),
                "authRequired": caller_spec.get("authRequired", False),
                "allowed": {
                    name: {
                        "risk": policy["capabilities"][name].get("risk"),
                        "gated": policy["capabilities"][name].get("gated", False),
                        "description": policy["capabilities"][name].get("description"),
                    }
                    for name in caps
                },
                "deniedCount": len(policy.get("capabilities", {})) - len(caps),
            }
            print(json.dumps(detail, indent=2))
            return EXIT_OK

        result = run_capability(
            policy,
            args.capability,
            args.caller,
            args.token,
            parse_params(args.param),
            args.approve,
            args.dry_run,
        )
        if args.json:
            print(json.dumps({
                "ok": result.ok, "exitCode": result.exit_code, "argv": result.argv,
                "stdout": result.stdout, "stderr": result.stderr,
            }, indent=2))
        else:
            if result.stdout:
                _write(sys.stdout, result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
            if result.stderr:
                _write(sys.stderr, result.stderr)
        return EXIT_OK if result.ok else EXIT_FAILED

    except PolicyError as exc:
        print(f"POLICY ERROR: {exc}", file=sys.stderr)
        return EXIT_POLICY
    except Denied as exc:
        try:
            audit(json.loads(read_policy_bytes()), {"decision": "denied", "reason": str(exc),
                                                    "caller": getattr(args, "caller", None),
                                                    "capability": getattr(args, "capability", None)})
        except Exception:  # noqa: BLE001 - auditing must never mask the denial
            pass
        print(f"DENIED: {exc}", file=sys.stderr)
        return EXIT_DENIED
    except BadInput as exc:
        print(f"BAD INPUT: {exc}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
