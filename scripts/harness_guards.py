#!/usr/bin/env python3
"""Harness guards — the controls that sit *underneath* the capability policy.

``harness.py`` answers "is this caller allowed to run this capability?". This
module answers the questions that remain *after* the answer is yes:

* :func:`safe_resolve`      — is this path really where it claims to be? (Windows-specific)
* :func:`bounded_read`      — can a child process exhaust our memory with output?
* :func:`chain_append`      — can someone rewrite the audit trail after the fact?
* :func:`redact`            — is a secret about to be written into that trail?
* :class:`Quota`            — can a looping model call the harness ten thousand times?
* :func:`mint_token`        — can a leaked token be replayed forever, on anything?

Every one of these is stdlib only, because the harness is the last thing that
should ever require a ``pip install`` to work.

Design note on failure direction: everything here fails CLOSED. A guard that
cannot decide raises rather than allowing, because "I could not tell whether
this path escapes the workspace" is not a reason to let it through.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------- errors


class GuardError(RuntimeError):
    """A guard refused the request. Callers map this onto their own Denied type."""


# ------------------------------------------------------------------- path confinement

# Reserved DOS device names. These are magic in EVERY directory on Windows: opening
# "C:/Alfred/CON" does not touch the filesystem at all, it opens the console device.
# A capability that writes to one either hangs forever or silently discards data, and
# a capability that reads one can block the harness indefinitely. There is no legitimate
# reason for a policy parameter to name one, so they are refused outright.
_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

# 8.3 short names ("PROGRA~1"). Windows resolves these to the long name, so a prefix
# check against "C:/Program Files" misses "C:/PROGRA~1". We expand them before checking.
_SHORT_NAME_RE = re.compile(r"~\d")


def _component_is_device(component: str) -> bool:
    """True if a path component names a DOS device.

    Windows strips trailing dots and spaces from path components before resolving
    them, so ``"NUL "`` and ``"NUL."`` both reach the device. It also ignores
    everything from the first dot for device purposes, so ``"NUL.txt"`` is still
    the device. Both quirks are handled here rather than trusted to the caller.
    """
    stripped = component.rstrip(" .")
    stem = stripped.split(".", 1)[0]
    return stem.upper() in _DEVICE_NAMES


def _expand_short_name(path: str) -> str:
    """Expand an 8.3 short path to its long form via GetLongPathNameW.

    ``GetLongPathNameW`` only resolves a path that **exists**. That matters more than it
    sounds: a capability writing a file that is not there yet, under a profile directory
    whose own name is shortened, would fail expansion and then be refused for containing
    ``~N`` — even though the only short component is a legitimate part of the user's home
    path. GitHub's Windows runners hit exactly this (``C:\\Users\\RUNNER~1\\...``), and so
    would any user whose username is long enough for Windows to shorten it.

    So the deepest **existing** ancestor is expanded and the not-yet-existing tail is
    rejoined. Only a short name that survives that is genuinely unresolvable, which is the
    case worth refusing. Ultron's ``safeResolve`` already walked ancestors for the same
    reason; this brings the two back in line.
    """
    if not WINDOWS:
        return path

    def _long(candidate: str) -> str | None:
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetLongPathNameW(candidate, buf, 32768)  # type: ignore[attr-defined]
            if 0 < length < 32768 and buf.value:
                return buf.value
        except Exception:  # noqa: BLE001 - API unavailable; fall through to the check below
            pass
        return None

    resolved = _long(str(path))
    if resolved:
        return resolved

    # The path does not exist. Expand the deepest ancestor that does, and keep the rest.
    current = Path(str(path))
    trailing: list[str] = []
    for _ in range(64):          # bounded: a path cannot be deeper than this in practice
        parent = current.parent
        if parent == current:
            break
        trailing.insert(0, current.name)
        current = parent
        expanded = _long(str(current))
        if expanded:
            return str(Path(expanded, *trailing))
    return str(path)


def safe_resolve(raw: str, base: Path) -> Path:
    """Resolve a user-supplied path to something safe to hand a subprocess.

    Refuses, in this order:

    1. **UNC and extended-length paths** (``\\\\server\\share``, ``\\\\?\\C:\\...``).
       ``\\\\?\\`` disables Windows' own path normalization, which is exactly the
       normalization the confinement check depends on. ``\\\\localhost\\C$\\`` reaches
       the whole disk while looking like a relative-ish path.
    2. **NTFS alternate data streams** (``file.txt:hidden``). The visible path passes
       confinement while the write lands in a stream nobody inspects — the CVE-2025-8088
       (WinRAR) pattern.
    3. **DOS device names** in any component (see :data:`_DEVICE_NAMES`).
    4. **Unresolvable 8.3 short names**, after trying to expand them.

    Then normalizes: NFC unicode, ``realpath`` (which follows junctions and symlinks,
    so a junction pointing out of the workspace resolves to its real target and is
    caught by the confinement check rather than tunnelling through it).

    Returns the resolved absolute path. Confinement against the allowed roots is a
    separate step — see :func:`inside_roots` — so the caller can report the two
    failures differently.
    """
    if not raw or not raw.strip():
        raise GuardError("empty path")

    # NFC first: two different byte sequences that display identically must not be able
    # to produce two different confinement answers.
    value = unicodedata.normalize("NFC", raw)

    if "\x00" in value:
        raise GuardError("path contains a NUL byte")

    slashed = value.replace("\\", "/")
    if slashed.startswith("//"):
        raise GuardError(
            "UNC and extended-length paths are refused (they bypass path normalization)"
        )

    # Alternate data streams: a colon anywhere except as the drive separator at index 1.
    # "C:/x" is fine; "C:/x:y" and "x:y" are not.
    drive, _, remainder = value.partition(":") if len(value) > 1 and value[1] == ":" else ("", "", value)
    if ":" in remainder:
        raise GuardError("alternate data streams (':' in a path component) are refused")
    del drive

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / value

    for component in Path(str(candidate)).parts:
        # Skip the drive/anchor component ("C:\\" or "/").
        if component.endswith(os.sep) or component.endswith(":") or component == "/":
            continue
        if _component_is_device(component):
            raise GuardError(f"path component '{component}' names a Windows device")
        # "." and ".." are the two components that legitimately consist of dots. They
        # are not an evasion: realpath collapses them, and a traversal that climbs out
        # of the workspace is then caught by the confinement check on the real target.
        # Refusing them here would reject ordinary relative paths for no security gain.
        if component not in (".", "..") and component != component.rstrip(" ."):
            raise GuardError(
                f"path component '{component}' has trailing dots/spaces "
                "(Windows strips these, so the checked path is not the used path)"
            )

    expanded = _expand_short_name(str(candidate))
    if _SHORT_NAME_RE.search(expanded.replace("\\", "/").split("/")[-1]) or any(
        _SHORT_NAME_RE.search(part) for part in expanded.replace("\\", "/").split("/")
    ):
        raise GuardError(
            "path contains an unresolvable 8.3 short name (~N); supply the long path"
        )

    # realpath, not resolve(): realpath follows junctions on Windows, which resolve()
    # historically did not. A junction is the cheapest way to point a path inside the
    # workspace at something outside it, and any user can create one without admin.
    return Path(os.path.realpath(expanded))


def inside_roots(path: Path, roots: Iterable[str]) -> bool:
    """True if ``path`` is inside one of ``roots``, comparing real paths.

    The roots are realpath'd too. If a root is itself reached through a junction,
    comparing a resolved child against an unresolved root would report a false
    escape and refuse legitimate work.
    """
    try:
        target = Path(os.path.realpath(str(path)))
    except OSError:
        return False
    for root in roots:
        try:
            root_real = Path(os.path.realpath(str(Path(root))))
            target.relative_to(root_real)
            return True
        except (ValueError, OSError):
            continue
    return False


# ----------------------------------------------------------------------- output caps


def bounded_read(stream, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` bytes of text from ``stream``.

    ``subprocess.run(capture_output=True)`` buffers the child's ENTIRE output in
    memory. A capability whose child writes 4 GB to stdout takes the harness down
    with it — a denial of service available to any caller that can run any
    capability at all, including the untrusted local model. So output is capped and
    the truncation is reported rather than hidden.
    """
    if stream is None:
        return "", False
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while total < limit:
        chunk = stream.read(min(65536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    else:
        # Hit the cap. Anything still coming is discarded, but we must keep draining
        # or the child blocks on a full pipe and never exits.
        truncated = bool(stream.read(1))
    raw = b"".join(chunks)
    if isinstance(raw, str):  # text-mode stream
        return raw, truncated
    return raw.decode("utf-8", errors="replace"), truncated


# ------------------------------------------------------------------- audit hash chain

CHAIN_GENESIS = "0" * 64


def canonical_json(value: Any) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace.

    Compact separators are not a style choice. Ultron's engine writes the same audit
    chain from Node, and ``JSON.stringify`` emits no spaces; Python's default
    ``json.dumps`` emits ``", "`` and ``": "``. Choosing the compact form is choosing
    the one serialization both languages produce identically without configuration,
    which is what makes a chain written by either engine verifiable by the other.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def chain_hash(prev: str, payload: str) -> str:
    """The link for one audit record: SHA-256 over the previous link + this payload."""
    return hashlib.sha256(f"{prev}\n{payload}".encode("utf-8")).hexdigest()


def _last_chain_state(path: Path) -> tuple[int, str]:
    """(sequence, hash) of the last record, or (0, GENESIS) for an empty/new log.

    Reads only the tail of the file. The audit trail grows without bound by design,
    so re-reading all of it on every single harness call would make the harness
    slower the longer it had been trusted — a bad incentive.
    """
    if not path.exists() or path.stat().st_size == 0:
        return 0, CHAIN_GENESIS
    with path.open("rb") as handle:
        size = path.stat().st_size
        window = min(size, 65536)
        handle.seek(size - window)
        tail = handle.read(window)
    for line in reversed(tail.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "chain" in record and "seq" in record:
            return int(record["seq"]), str(record["chain"])
        # A pre-chain record: legitimate history from before chaining existed.
        # Start the chain from genesis at this point rather than refusing to log.
        return 0, CHAIN_GENESIS
    return 0, CHAIN_GENESIS


def chain_append(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append ``record`` to a hash-chained JSONL log and return the stored record.

    What this stops: silent *edits* and *deletions* inside the trail. Change or
    remove any record and every following link mismatches, so `audit-verify` names
    the exact line where history was rewritten.

    What this does NOT stop: an attacker who can write the file truncating the tail
    and continuing a fresh valid chain from that point. Detecting that needs a
    witness the attacker cannot reach — so :func:`chain_checkpoint` writes the head
    hash to a separate file, and the honest limitation is documented rather than
    papered over.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    seq, prev = _last_chain_state(path)
    body = {"seq": seq + 1, "prev": prev, **record}
    payload = canonical_json(body)
    body["chain"] = chain_hash(prev, payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(body, ensure_ascii=False) + "\n")
    return body


def chain_verify(path: Path) -> dict[str, Any]:
    """Walk the whole chain and report the first break, if any."""
    if not path.exists():
        return {"ok": True, "records": 0, "chained": 0, "legacy": 0, "note": "no audit log yet"}
    prev = CHAIN_GENESIS
    total = chained = legacy = 0
    expected_seq = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        total += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return {"ok": False, "records": total, "chained": chained, "legacy": legacy,
                    "brokenAt": lineno, "reason": "line is not valid JSON"}
        if "chain" not in record:
            legacy += 1
            continue  # history from before chaining; not covered, and reported as such
        stored = record.pop("chain")
        seq = record.get("seq")
        if seq != expected_seq + 1:
            return {"ok": False, "records": total, "chained": chained, "legacy": legacy,
                    "brokenAt": lineno, "reason": f"sequence jumped: expected {expected_seq + 1}, got {seq}"}
        payload = canonical_json(record)
        # The legacy form is Python's default `json.dumps(sort_keys=True)`, which spaces
        # its separators. A handful of records were written that way before the format
        # was pinned to the compact form for cross-engine parity. Accepting either keeps
        # real history verifiable instead of reporting authentic records as tampered -
        # and it cannot be used to forge anything, because both candidates are still
        # full hashes over the same content.
        legacy_payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if not (hmac.compare_digest(chain_hash(prev, payload), str(stored))
                or hmac.compare_digest(chain_hash(prev, legacy_payload), str(stored))):
            return {"ok": False, "records": total, "chained": chained, "legacy": legacy,
                    "brokenAt": lineno, "reason": "hash mismatch — this record was altered"}
        prev = str(stored)
        expected_seq = int(seq)
        chained += 1
    return {"ok": True, "records": total, "chained": chained, "legacy": legacy, "head": prev}


# ------------------------------------------------------------------- legacy sealing


def legacy_region(path: Path) -> tuple[int, bytes]:
    """The contiguous run of unchained records at the head of the log.

    These are records written before chaining existed. They cannot be retroactively
    chained: chaining means each record embeds the hash of the one before it, so adding
    that would mean rewriting every legacy record — and rewriting an audit trail to make it
    verifiable is self-defeating. The bytes stay exactly as they were written.

    What *can* be done is seal them: take one hash over the whole region and keep it
    somewhere the attacker cannot reach. That does not prove the records were true when
    written, and it does not turn them into chained records. It proves they have not been
    altered *since the seal*, which is the only honest claim available.
    """
    if not path.exists():
        return 0, b""
    count = 0
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                if "chain" in json.loads(raw):
                    break          # the chained era starts here
            except json.JSONDecodeError:
                pass               # unparseable lines are still part of the history
            chunks.append(raw)
            count += 1
    return count, b"".join(chunks)


def seal_legacy(path: Path, key: bytes, seal_path: Path) -> dict[str, Any]:
    """Seal the legacy region with an HMAC and record it.

    The MAC uses the harness signing key rather than a bare SHA-256, and that choice is the
    whole point. A plain hash stored next to the log is no defence: whoever edits a legacy
    record can recompute the hash and update the anchor. An HMAC cannot be recomputed
    without the key, which lives in ``secrets/`` where every agent is denied access.
    """
    count, blob = legacy_region(path)
    if count == 0:
        raise GuardError("there is no legacy region to seal")
    mac = hmac.new(key, blob, hashlib.sha256).hexdigest()
    payload = {
        "sealedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "records": count,
        "bytes": len(blob),
        "mac": mac,
        "note": "Proves the pre-chain records have not been altered since this seal. It "
                "does not prove they were true when written, and does not make them "
                "chained records.",
    }
    seal_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = seal_path.with_suffix(seal_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, seal_path)
    return payload


def verify_legacy_seal(path: Path, key: bytes | None, seal_path: Path) -> dict[str, Any]:
    """Check the legacy region against its seal."""
    count, blob = legacy_region(path)
    if count == 0:
        return {"legacyRecords": 0, "sealed": True,
                "note": "no pre-chain records; the chain covers everything"}
    if not seal_path.exists():
        return {"legacyRecords": count, "sealed": False,
                "note": "pre-chain records are unsealed; run 'harness seal-legacy'"}
    if key is None:
        return {"legacyRecords": count, "sealed": False,
                "note": "cannot verify the seal without the signing key"}
    try:
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"legacyRecords": count, "sealed": False, "intact": False,
                "note": f"seal is unreadable: {exc}"}

    expected = hmac.new(key, blob, hashlib.sha256).hexdigest()
    intact = hmac.compare_digest(expected, str(payload.get("mac", "")))
    out = {"legacyRecords": count, "sealed": True, "intact": intact,
           "sealedAt": payload.get("sealedAt"), "sealedRecords": payload.get("records")}
    if not intact:
        # Distinguish "someone appended more legacy records" (impossible in normal
        # operation, since new records are chained) from "someone edited history". Both
        # are worth knowing, and they are different problems.
        out["reason"] = (
            f"the pre-chain region no longer matches its seal "
            f"(sealed {payload.get('records')} records / {payload.get('bytes')} bytes, "
            f"now {count} records / {len(blob)} bytes)"
        )
    return out


def chain_checkpoint(path: Path, out: Path) -> dict[str, Any]:
    """Record the current chain head somewhere separate, as an external witness.

    Without this, tail truncation is undetectable. With it, a truncated log has a
    head that no longer matches the last checkpoint.
    """
    state = chain_verify(path)
    if not state.get("ok"):
        raise GuardError(f"refusing to checkpoint a broken chain: {state.get('reason')}")
    payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "records": state["records"],
               "chained": state["chained"], "head": state.get("head", CHAIN_GENESIS)}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


# --------------------------------------------------------------------------- redaction

# Allowlist, not denylist. A denylist of secret-shaped regexes must anticipate every
# format a secret can take; an allowlist only needs to know which fields are boring.
# When a capability gains a new parameter tomorrow, the allowlist withholds its value
# by default instead of publishing it into an append-only file forever.
DEFAULT_LOGGABLE_PARAMS = ("path", "spec", "pipeline", "subject", "predicate", "days", "query")

_SECRET_SHAPED = (
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|authorization)"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),          # long hex — keys, hashes, HMACs
    re.compile(r"\b(sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{10,}"),  # known key prefixes
)

REDACTED = "[REDACTED]"


def redact(params: dict[str, str], loggable: Iterable[str] | None = None) -> dict[str, str]:
    """Return ``params`` with every non-allowlisted or secret-shaped value replaced.

    Names are always kept: knowing *that* a ``token`` parameter was supplied is
    useful for an audit and reveals nothing. Values are the risk.
    """
    allowed = set(loggable if loggable is not None else DEFAULT_LOGGABLE_PARAMS)
    out: dict[str, str] = {}
    for name, value in params.items():
        if name not in allowed:
            out[name] = REDACTED
            continue
        text = str(value)
        if any(pattern.search(name) for pattern in _SECRET_SHAPED) or any(
            pattern.search(text) for pattern in _SECRET_SHAPED
        ):
            out[name] = REDACTED
            continue
        out[name] = text
    return out


# ------------------------------------------------------------------------------ quota


class Quota:
    """A persisted token bucket, one bucket per caller.

    Why a bucket and not a fixed window: a fixed window lets a caller spend its
    whole allowance in the last second of one window and again in the first second
    of the next, which is exactly the burst a runaway loop produces. A bucket
    smooths that while still permitting a legitimate short burst.

    State lives in one JSON file. It is written atomically (temp file + replace)
    because two harness processes can race, and a torn quota file that fails to
    parse must not be able to either grant infinite calls or deny all of them —
    an unreadable bucket is treated as full, and the next successful write repairs it.
    """

    def __init__(self, path: Path, now: float | None = None) -> None:
        self.path = path
        self._now = now if now is not None else time.time()

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def check_and_consume(self, caller: str, per_minute: int, burst: int | None = None) -> dict[str, Any]:
        """Consume one token for ``caller``. Raises :class:`GuardError` when empty."""
        if per_minute <= 0:
            return {"limited": False, "reason": "no limit configured"}
        capacity = float(burst if burst is not None else per_minute)
        state = self._load()
        bucket = state.get(caller) or {}
        tokens = float(bucket.get("tokens", capacity))
        last = float(bucket.get("ts", self._now))
        elapsed = max(0.0, self._now - last)
        tokens = min(capacity, tokens + elapsed * (per_minute / 60.0))

        if tokens < 1.0:
            wait = (1.0 - tokens) / (per_minute / 60.0)
            state[caller] = {"tokens": tokens, "ts": self._now}
            self._save(state)
            raise GuardError(
                f"rate limit exceeded for caller '{caller}': "
                f"{per_minute}/min (burst {int(capacity)}); retry in {wait:.1f}s"
            )

        tokens -= 1.0
        state[caller] = {"tokens": tokens, "ts": self._now}
        self._save(state)
        return {"limited": True, "remaining": round(tokens, 2), "capacity": int(capacity)}


# ------------------------------------------------------------------- scoped tokens

TOKEN_VERSION = "aht2"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint_token(key: bytes, caller: str, scopes: Iterable[str], ttl_seconds: int,
               now: float | None = None) -> str:
    """Mint a scope-bound, expiring bearer token.

    The flat token this replaces has two problems. It never expires, so one leak is
    permanent; and it is not bound to anything, so a token issued for ``graph-recall``
    authenticates a call to ``graph-assert`` just as well. That second one is the
    confused-deputy problem in its natural habitat: the untrusted local model holds a
    valid token, and a prompt injection only has to talk it into using that token on a
    capability the *attacker* chose.

    So the capability list and the expiry are inside the MAC, not next to it.

    Format: ``aht2.<caller>.<iat>.<exp>.<nonce>.<scopes>.<mac>``. Everything before the
    MAC is signed, so none of it can be edited — widening the scopes or pushing the expiry
    out invalidates the token.

    ``iat`` (issued-at) exists for revocation. Without it the only way to revoke is to
    name each leaked token individually, which is useless in the case that actually
    matters: "a token leaked and I do not know which one". With it, revoking a whole
    caller is one timestamp — see :func:`revoke_caller`.
    """
    stamp = int(now if now is not None else time.time())
    exp = stamp + int(ttl_seconds)
    nonce = _b64(os.urandom(9))
    scope_text = "+".join(sorted(scopes)) or "*"
    body = f"{TOKEN_VERSION}.{caller}.{stamp}.{exp}.{nonce}.{scope_text}"
    mac = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def verify_token(key: bytes, token: str, caller: str, capability: str,
                 now: float | None = None,
                 revocations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify a scoped token. Raises :class:`GuardError` with a precise reason.

    Returns the token's claims on success so the caller can audit which token was used
    (by nonce — never the token itself).
    """
    parts = token.split(".")
    if len(parts) != 7 or parts[0] != TOKEN_VERSION:
        raise GuardError("not a scoped harness token")
    _, tok_caller, iat_text, exp_text, nonce, scope_text, mac = parts

    body = ".".join(parts[:6])
    expected = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    # Constant-time, and checked BEFORE any claim is trusted: reading the expiry or
    # the scopes out of an unverified token would let an attacker steer the error
    # messages, and error messages are an oracle.
    if not hmac.compare_digest(expected, mac):
        raise GuardError("token signature is invalid")

    if tok_caller != caller:
        raise GuardError(f"token was issued for caller '{tok_caller}', not '{caller}'")

    try:
        exp = int(exp_text)
        iat = int(iat_text)
    except ValueError as exc:
        raise GuardError("token timestamps are malformed") from exc
    stamp = int(now if now is not None else time.time())
    if stamp >= exp:
        raise GuardError(f"token expired {stamp - exp}s ago")

    # Revocation is checked after the signature (so an unverified token cannot probe the
    # list) and after expiry (so an already-dead token gives the more useful message).
    if revocations:
        if nonce in (revocations.get("nonces") or {}):
            raise GuardError("token has been revoked")
        epoch = (revocations.get("callerEpochs") or {}).get(tok_caller)
        if epoch is not None and iat < int(epoch):
            raise GuardError(
                f"every token issued for '{tok_caller}' before {int(epoch)} was revoked"
            )

    scopes = scope_text.split("+")
    if scope_text != "*" and capability not in scopes:
        raise GuardError(
            f"token is scoped to {scopes} and does not cover '{capability}'"
        )
    return {"caller": tok_caller, "iat": iat, "exp": exp, "nonce": nonce, "scopes": scopes,
            "expiresIn": exp - stamp}


def token_claims(key: bytes, token: str) -> dict[str, Any]:
    """Verify only a token's signature and return its claims.

    Used by ``harness revoke``: revoking a token requires reading its nonce, and reading
    a claim out of an unverified token would let anyone poison the revocation list with
    invented nonces. An expired token is still parseable here on purpose — revoking one
    is harmless, and refusing would make the command fail exactly when someone is
    responding to a leak in a hurry.
    """
    parts = token.split(".")
    if len(parts) != 7 or parts[0] != TOKEN_VERSION:
        raise GuardError("not a scoped harness token")
    body = ".".join(parts[:6])
    expected = hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, parts[6]):
        raise GuardError("token signature is invalid; refusing to revoke an unverified token")
    return {"caller": parts[1], "iat": int(parts[2]), "exp": int(parts[3]),
            "nonce": parts[4], "scopes": parts[5].split("+")}


# ------------------------------------------------------------------------- revocation


def load_revocations(path: Path, now: float | None = None) -> dict[str, Any]:
    """Read the revocation store, dropping entries that expiry has made redundant.

    A revoked nonce only needs to be remembered until the token would have expired
    anyway; keeping it longer grows a file that is read on every authenticated call. The
    pruning is done on read rather than by a scheduled job so the list cannot grow without
    bound just because nobody remembered to clean it.

    A missing or unreadable store means "nothing is revoked". That is the fail-OPEN
    direction and it is deliberate: this file cannot be reached by any agent (it lives in
    ``secrets/``), and treating an unreadable store as "everything is revoked" would let a
    single corrupt write lock the Owner out of his own harness. The integrity that matters
    is protected by the directory, not by this function.
    """
    stamp = int(now if now is not None else time.time())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"nonces": {}, "callerEpochs": {}}
    nonces = {n: int(e) for n, e in (raw.get("nonces") or {}).items() if int(e) > stamp}
    return {"nonces": nonces, "callerEpochs": raw.get("callerEpochs") or {}}


def _save_revocations(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def revoke_nonce(path: Path, nonce: str, exp: int, now: float | None = None) -> dict[str, Any]:
    """Revoke one specific token by its nonce."""
    state = load_revocations(path, now)
    state["nonces"][nonce] = int(exp)
    _save_revocations(path, state)
    return state


def revoke_caller(path: Path, caller: str, now: float | None = None) -> dict[str, Any]:
    """Revoke every token already issued for ``caller``.

    This is the break-glass case and the reason tokens carry an issue time: when a
    credential leaks you usually do not know *which* one, only that something for that
    caller is loose. One timestamp invalidates the whole generation, and freshly minted
    tokens keep working because their ``iat`` is later.

    The epoch is stored as ``now + 1``, not ``now``. Timestamps here have one-second
    resolution, so a token minted in the *same second* as the revocation would otherwise
    have ``iat == epoch`` and survive a check for ``iat < epoch`` — which is precisely the
    token most likely to be the leaked one, since a leak and its response happen close
    together. Rounding up revokes marginally too much rather than too little; the cost is
    that a replacement minted within the same second is also refused, which is a retry
    rather than a security hole.
    """
    stamp = int(now if now is not None else time.time())
    state = load_revocations(path, stamp)
    state["callerEpochs"][caller] = stamp + 1
    _save_revocations(path, state)
    return state


# ------------------------------------------------------------------------ policy diff


def _flatten_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Reduce a policy to a flat map of meaningful claims.

    A textual diff of the JSON is the wrong tool: reformatting, key reordering and
    indentation changes all show up as differences while a caller silently gaining a
    capability might be one line buried among them. Flattening to claims means the diff
    reports what the policy *means* rather than how it is spelled.
    """
    flat: dict[str, Any] = {}
    settings = policy.get("settings", {})
    for key, value in settings.items():
        if isinstance(value, (dict, list)):
            flat[f"settings.{key}"] = canonical_json(value)
        else:
            flat[f"settings.{key}"] = value

    for name, spec in (policy.get("callers") or {}).items():
        flat[f"caller.{name}.trust"] = spec.get("trust")
        flat[f"caller.{name}.authRequired"] = bool(spec.get("authRequired", False))
        flat[f"caller.{name}.requireScopedToken"] = bool(spec.get("requireScopedToken", False))
        flat[f"caller.{name}.rateLimit"] = canonical_json(spec.get("rateLimit") or {})
        # Capabilities are compared as a SET, not a list: reordering an allowlist is not a
        # change to what it permits, and reporting it as one trains the reviewer to skim.
        for cap in sorted(set(spec.get("capabilities") or [])):
            flat[f"caller.{name}.grants.{cap}"] = True

    for name, spec in (policy.get("capabilities") or {}).items():
        flat[f"capability.{name}.risk"] = spec.get("risk")
        flat[f"capability.{name}.gated"] = bool(spec.get("gated", False))
        flat[f"capability.{name}.command"] = spec.get("command")
        flat[f"capability.{name}.args"] = canonical_json(spec.get("args") or [])
        flat[f"capability.{name}.params"] = canonical_json(spec.get("params") or {})
        # The egress declaration is a policy claim like any other, so the diff has to see
        # it. It did not at first, which meant adding `network: true` to fourteen
        # capabilities showed up as "1 addition, 0 changes" — a review tool that cannot see a
        # change gives false assurance, which is worse than having no review tool.
        flat[f"capability.{name}.network"] = bool(spec.get("network", True))

    for group, values in (policy.get("forbidden") or {}).items():
        if isinstance(values, list):
            for value in values:
                flat[f"forbidden.{group}.{value}"] = True
    return flat


# Claims where *gaining* something is a privilege increase, and claims where *losing*
# something is. Getting this backwards is how a review tool reassures you about the change
# that mattered, so the direction is explicit rather than inferred from a heuristic.
_WIDENING_PREFIXES = ("caller.", "capability.")
_TIGHTENING_KEYS = ("authRequired", "requireScopedToken", "gated")


def diff_policy(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two policies semantically and classify each change by direction.

    Returns ``{"added": [...], "removed": [...], "changed": [...], "privilegeIncreases": [...]}``.

    ``privilegeIncreases`` is the field that matters. Signing a policy is the one step in
    the whole design where a human blesses a change, and until now it was a rubber stamp:
    ``harness sign`` computed an HMAC over whatever bytes happened to be on disk and said
    nothing about what they contained. A model that edited the policy could not forge a
    signature — but it could wait for the Owner to re-sign for an unrelated reason and get
    its edit blessed for free.
    """
    old_flat, new_flat = _flatten_policy(old), _flatten_policy(new)
    added, removed, changed, escalations = [], [], [], []

    for key in sorted(set(new_flat) - set(old_flat)):
        entry = {"key": key, "value": new_flat[key]}
        added.append(entry)
        if key.startswith(_WIDENING_PREFIXES):
            if ".grants." in key:
                escalations.append({**entry, "why": "a caller gained a capability"})
            elif key.startswith("capability."):
                escalations.append({**entry, "why": "a new capability exists"})

    for key in sorted(set(old_flat) - set(new_flat)):
        entry = {"key": key, "value": old_flat[key]}
        removed.append(entry)
        if key.startswith("forbidden."):
            escalations.append({**entry, "why": "a forbidden pattern was removed"})
        elif any(key.endswith(t) for t in _TIGHTENING_KEYS):
            escalations.append({**entry, "why": "a restriction was removed"})

    for key in sorted(set(old_flat) & set(new_flat)):
        if old_flat[key] == new_flat[key]:
            continue
        entry = {"key": key, "from": old_flat[key], "to": new_flat[key]}
        changed.append(entry)
        # A restriction turning off is an escalation; turning on is not.
        if any(key.endswith(t) for t in _TIGHTENING_KEYS) and not new_flat[key]:
            escalations.append({**entry, "why": "a restriction was switched off"})
        elif key.endswith(".trust"):
            order = {"untrusted": 0, "low": 1, "medium": 2, "high": 3}
            if order.get(str(new_flat[key]), -1) > order.get(str(old_flat[key]), -1):
                escalations.append({**entry, "why": "a caller was promoted"})
        elif key.endswith(".command") or key.endswith(".args"):
            escalations.append({**entry, "why": "what a capability executes has changed"})
        elif key.endswith(".network") and new_flat[key]:
            # false -> true means a capability that was confined to no network now reaches it.
            escalations.append({**entry, "why": "a capability was granted network access"})
        elif key in ("settings.denyByDefault", "settings.requireSignature") and not new_flat[key]:
            escalations.append({**entry, "why": "a core safety setting was disabled"})

    return {"added": added, "removed": removed, "changed": changed,
            "privilegeIncreases": escalations,
            "identical": not (added or removed or changed)}


# ---------------------------------------------------------------------- review ledger


def record_review(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append one signing decision to the review ledger.

    ``sign --review`` answers "what changed since the last signature?". It cannot answer
    "was that last signature itself ever reviewed?" — and the first signature on any clone
    establishes a baseline nobody vetted. That gap is unavoidable, but it does not have to be
    invisible: the ledger records every signing, whether it was reviewed, and which
    escalations were accepted, so the provenance of the current policy is inspectable instead
    of being a matter of memory.

    Hash-chained, like the audit trail, and for the same reason: a record of approvals that
    can be edited afterwards is a record of whatever the last editor preferred.
    """
    return chain_append(path, entry)


def review_history(path: Path, limit: int = 20) -> dict[str, Any]:
    """The signing history, newest last, plus a summary of what is known and what is not."""
    state = chain_verify(path)
    entries: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    reviewed = sum(1 for e in entries if e.get("reviewed"))
    accepted = sum(1 for e in entries if e.get("acceptedEscalations"))
    return {
        "ledger": str(path),
        "chainOk": state.get("ok"),
        "signings": len(entries),
        "reviewed": reviewed,
        "unreviewed": len(entries) - reviewed,
        "signingsWithAcceptedEscalations": accepted,
        "baseline": entries[0].get("ts") if entries else None,
        "baselineWasReviewed": bool(entries[0].get("reviewed")) if entries else None,
        "recent": entries[-limit:],
        "note": "A signing recorded as unreviewed is not necessarily wrong - `sign` without "
                "--review is legitimate for a change you just made deliberately. It means no "
                "diff was examined at that point, which is a fact worth being able to look up.",
    }


# ------------------------------------------------------------------------ policy lint

def lint_policy(policy: dict[str, Any]) -> list[dict[str, str]]:
    """Static checks on the policy itself. Returns a list of findings.

    A signed policy is a policy nobody can tamper with. It is not necessarily a
    policy that says what its author meant. These checks catch the mistakes that a
    signature happily certifies: a caller granted a capability that no longer
    exists, an untrusted caller handed something that writes, a parameter declared
    and never used (so validation never runs on it), a placeholder used and never
    declared (so it can never resolve).
    """
    findings: list[dict[str, str]] = []
    caps = policy.get("capabilities", {})
    callers = policy.get("callers", {})
    settings = policy.get("settings", {})

    def add(level: str, where: str, message: str) -> None:
        findings.append({"level": level, "where": where, "message": message})

    reachable: set[str] = set()
    for name, spec in callers.items():
        granted = spec.get("capabilities", [])
        trust = spec.get("trust")
        if "*" in granted:
            reachable |= set(caps)
            if trust != "high":
                add("error", f"callers.{name}", f"wildcard '*' granted to trust={trust}; only high trust should hold it")
        for cap in granted:
            if cap == "*":
                continue
            if cap not in caps:
                add("error", f"callers.{name}", f"grants '{cap}', which is not a defined capability")
                continue
            reachable.add(cap)
            spec_cap = caps[cap]
            if spec_cap.get("gated") and trust != "high":
                add("error", f"callers.{name}",
                    f"grants gated capability '{cap}' to trust={trust}; the gate would always refuse it")
            if trust == "untrusted" and spec_cap.get("risk") not in ("read", None):
                add("error", f"callers.{name}",
                    f"grants '{cap}' (risk={spec_cap.get('risk')}) to an untrusted caller")
        if spec.get("trust") in ("untrusted", "low") and not spec.get("authRequired"):
            add("warn", f"callers.{name}", f"trust={spec.get('trust')} but authRequired is false")

    for name, spec in caps.items():
        if name not in reachable:
            add("warn", f"capabilities.{name}", "no caller can run this capability (dead entry)")
        if not spec.get("description"):
            add("warn", f"capabilities.{name}", "has no description; `harness list` will show a blank")
        if not spec.get("risk"):
            add("warn", f"capabilities.{name}", "has no declared risk level")
        declared = set(spec.get("params", {}))
        used: set[str] = set()
        for arg in spec.get("args", []):
            used |= set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", str(arg)))
        for missing in sorted(used - declared):
            add("error", f"capabilities.{name}", f"argv uses {{{missing}}} but does not declare it as a param")
        for unused in sorted(declared - used):
            add("warn", f"capabilities.{name}", f"declares param '{unused}' but never uses it in argv")
        if spec.get("risk") in ("write", "destructive", "system") and not spec.get("gated"):
            add("warn", f"capabilities.{name}", f"risk={spec.get('risk')} but not gated")
        if "network" not in spec:
            # Absent means "assumed to need the network", which is the permissive default.
            # A capability that never declares it therefore never gets isolated, so the
            # silence is worth a nudge rather than being treated as a decision.
            add("warn", f"capabilities.{name}",
                "does not declare `network`; assumed true, so it will never be egress-isolated")

    # Which untrusted-reachable capabilities can still reach the network. Reported as a
    # warning, not an error: the live policy legitimately grants the untrusted model
    # `graph-recall`, which needs LM Studio on loopback to embed a query. The point is that
    # the fact is visible rather than that the linter forbids it.
    for name, spec in callers.items():
        if spec.get("trust") != "untrusted":
            continue
        granted = spec.get("capabilities", [])
        reach = sorted(c for c in granted if c in caps and caps[c].get("network", True))
        if reach:
            add("warn", f"callers.{name}",
                f"untrusted caller can reach the network via {reach}")

    if not settings.get("denyByDefault", False):
        add("error", "settings", "denyByDefault is not true")
    if not settings.get("requireSignature", False):
        add("error", "settings", "requireSignature is not true")
    if int(settings.get("maxOutputBytes", 0)) <= 0:
        add("warn", "settings", "no maxOutputBytes cap; child output is buffered unbounded in memory")
    if not settings.get("allowedWorkspaceRoots"):
        add("error", "settings", "allowedWorkspaceRoots is empty; path confinement has nothing to confine to")
    return findings
