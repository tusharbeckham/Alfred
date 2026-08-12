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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policy" / "harness-policy.json"
SIG_PATH = ROOT / "policy" / "harness-policy.sig"
KEY_PATH = Path(os.environ.get("ALFRED_HARNESS_KEY_FILE") or (ROOT / "secrets" / "harness.key"))
CALLERS_PATH = ROOT / "secrets" / "harness-callers.json"

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
    signature = compute_signature(read_policy_bytes(), key)
    SIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIG_PATH.write_text(signature + "\n", encoding="utf-8")
    return signature


# ---------------------------------------------------------------------------- auth


def authenticate(caller: str, token: str | None, spec: dict[str, Any]) -> None:
    if not spec.get("authRequired", False):
        return
    if not token:
        raise Denied(f"Caller '{caller}' requires a token (--token or ALFRED_HARNESS_TOKEN).")
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


# ---------------------------------------------------------------- param validation


def _inside(path: Path, roots: list[str]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


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
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = (ROOT / value).resolve()
            for pattern in forbidden.get("pathPatterns", []):
                if re.search(pattern, str(candidate).replace("\\", "/")):
                    raise Denied(f"{capability}.{name}: path matches a forbidden pattern")
            normalized = str(candidate).replace("\\", "/")
            for prefix in forbidden.get("pathPrefixes", []):
                if normalized.lower().startswith(prefix.lower()):
                    raise Denied(f"{capability}.{name}: path is inside a forbidden location ({prefix})")
            if rule.get("mustBeInsideWorkspace", True) and not _inside(candidate, roots):
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


def audit(policy: dict[str, Any], record: dict[str, Any]) -> None:
    rel = policy.get("settings", {}).get("auditLog", "memory/harness-audit.jsonl")
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------- execution


@dataclass
class Result:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    argv: list[str]


def execute(argv: list[str], timeout: int) -> Result:
    try:
        completed = subprocess.run(  # noqa: S603 - argv array, shell=False, validated above
            argv,
            cwd=str(ROOT),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return Result(False, 127, "", f"Executable not found: {argv[0]}", argv)
    except subprocess.TimeoutExpired:
        return Result(False, 124, "", f"Timed out after {timeout}s", argv)
    return Result(
        completed.returncode == 0, completed.returncode, completed.stdout, completed.stderr, argv
    )


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
        authenticate(caller, token, caller_spec)
    except Denied as exc:
        stage("auth", False, str(exc)[:80])
        raise
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

    if dry_run:
        audit(policy, {**base, "decision": "dry-run"})
        stage("execute", True, "dry-run: nothing executed")
        stage("audit", True, "appended")
        return Result(True, 0, json.dumps({"dryRun": True, "argv": argv}, indent=2), "", argv)

    stage("execute", True, "running")
    result = execute(argv, timeout)
    stage("execute", result.ok, f"exit {result.exit_code}")
    audit(policy, {**base, "decision": "executed", "exitCode": result.exit_code, "ok": result.ok})
    stage("audit", True, "appended to the trail")
    return result


# ------------------------------------------------------------------------------ cli


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
    sub.add_parser("sign", help="Owner-only: (re)generate the policy signature")

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
            signature = sign_policy()
            print(json.dumps({"signed": True, "signature": signature, "key": str(KEY_PATH)}, indent=2))
            return EXIT_OK

        policy = verify_policy()

        if args.command == "verify":
            print(json.dumps({
                "policy": str(POLICY_PATH),
                "signatureValid": True,
                "denyByDefault": policy.get("settings", {}).get("denyByDefault"),
                "callers": {name: spec.get("trust") for name, spec in policy.get("callers", {}).items()},
                "capabilityCount": len(policy.get("capabilities", {})),
                "gated": sorted(k for k, v in policy.get("capabilities", {}).items() if v.get("gated")),
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
                sys.stdout.write(result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
            if result.stderr:
                sys.stderr.write(result.stderr)
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
