#!/usr/bin/env python3
"""Alfred model backends - shared brain assembly plus one executor per backend.

This is the seam between Alfred's *cognition* (the agent configs, identity prompts,
always-on steering, skills, and memory that live in `.kiro/`) and the *models* that
actually run a task. Both the DAG engine (`scripts/workflow.py`) and the local CLI
(`scripts/ultron.py`) import this module, so prompt assembly and model routing exist
in exactly one place.

Backends (all share one call signature - `(agent, task, timeout=None) -> str`):

  claude  `claude -p` (Claude Code CLI). Real tools on this PC; uses the Claude
          subscription. Resolves the agent from `.claude/agents/<name>.md` when the
          generated config exists, else injects the identity via --append-system-prompt.
  api     POST https://api.anthropic.com/v1/messages. Text-only (no tools), but runs
          from any machine that has ANTHROPIC_API_KEY.
  local   LM Studio's OpenAI-compatible endpoint on localhost. Free and offline.
  kiro    `kiro-cli chat --no-interactive` (the original path; unchanged).
  dry     An echo that never spawns anything.

`resolve_backend()` picks one automatically: claude -> api -> local -> kiro.

Design constraints (deliberate, matching the rest of the repo):
  * Standard library only. No `pip install` step, works fully offline, and the
    `local` backend never needs a network. That rules out the `anthropic` SDK, so
    the Anthropic path is a hand-rolled urllib client against /v1/messages.
  * Pure functions where practical (`resolve_backend`, `resolve_agent_model`,
    `assemble_system_prompt`) so `scripts/test_backends.py` can cover them with no
    network, no subprocess, and no model.
  * `ALFRED_ROOT` (or the repo layout) resolves every path, so a clone works from
    any directory on any machine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

__all__ = [
    "ROOT", "AGENTS_DIR", "STEERING_DIR", "SKILLS_DIR", "SCRIPTS_DIR",
    "BackendError", "Executor",
    "strip_frontmatter", "resolve_uri", "parse_skill_names",
    "list_agent_files", "load_agent", "load_steering", "load_skills", "recall_memory",
    "assemble_system_prompt", "DEFAULT_PREAMBLE",
    "MODEL_MAP", "PRICES", "resolve_agent_model", "load_model_overrides",
    "endpoint_models", "resolve_model", "call_local", "call_local_stream",
    "have_claude_cli", "have_api_key", "have_kiro_cli", "local_endpoint_up",
    "claude_workspace_trusted", "claude_trust_hint",
    "BACKENDS", "default_probes", "resolve_backend", "backend_report",
    "make_executor", "echo_executor", "kiro_executor",
]


# --------------------------------------------------------------------------- paths
def _repo_root() -> Path:
    """Repo root: $ALFRED_ROOT if set, else the parent of scripts/."""
    env = os.environ.get("ALFRED_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


ROOT = _repo_root()
AGENTS_DIR = ROOT / ".kiro" / "agents"
BRAINS_DIR = ROOT / ".kiro" / "brains"
STEERING_DIR = ROOT / ".kiro" / "steering"
SKILLS_DIR = ROOT / ".kiro" / "skills"
SETTINGS_DIR = ROOT / ".kiro" / "settings"
SCRIPTS_DIR = ROOT / "scripts"
CLAUDE_AGENTS_DIR = ROOT / ".claude" / "agents"

DEFAULT_LOCAL_MODEL = os.environ.get("ULTRON_MODEL", "alfred-coder-7b")
DEFAULT_LOCAL_ENDPOINT = os.environ.get("ULTRON_ENDPOINT", "http://localhost:1234/v1")

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_VERSION = "2023-06-01"
# Opus 5's safety classifiers can decline a request (HTTP 200 + stop_reason
# "refusal"). Server-side fallback re-runs the declined request on Anthropic's
# recommended substitute inside the same call, so a false positive on benign
# security/life-sciences work still gets answered. Set ALFRED_API_FALLBACKS=0 to disable.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class BackendError(Exception):
    """Raised when a backend is unusable or a model call fails unrecoverably."""


# --------------------------------------------------------------------------- helpers
def strip_frontmatter(text: str) -> "tuple[str, dict]":
    """Split leading YAML-ish frontmatter (--- ... ---) from a markdown body.

    Returns (body, meta) where meta holds simple `key: value` pairs found in the block.
    """
    meta: "dict[str, str]" = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end]
            body = text[end + 4:]
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            return body.lstrip("\n"), meta
    return text, meta


def resolve_uri(uri: str) -> Path:
    """Resolve a `file:///C:/Alfred/...` URI (or a plain/relative path) to a Path.

    Portability: the `.kiro/agents/*.json` configs hardcode `file:///C:/Alfred/...`
    because that is what Kiro itself reads. When that absolute path does not exist -
    a clone on another machine, another drive, or a CI checkout - we retry the same
    tail relative to the repo root, so the identity prompts still load.
    """
    if uri.startswith("file:///"):
        p = uri[len("file:///"):]
    elif uri.startswith("file://"):
        p = uri[len("file://"):]
    else:
        p = uri
    path = Path(p)
    if not path.is_absolute():
        return (ROOT / p).resolve()
    if path.exists():
        return path
    relocated = _relocate(path)
    return relocated if relocated is not None else path


def _relocate(path: Path) -> "Path | None":
    """Map an absolute path from another checkout onto this ROOT, if we can.

    Anchors on the first `.kiro`/`.claude`/`hooks`/`scripts`/`memory` segment and
    rebuilds the tail under ROOT. Returns None when no anchor matches or the
    rebuilt path does not exist (so the caller can report the original).
    """
    parts = path.parts
    for anchor in (".kiro", ".claude", "hooks", "scripts", "memory", "workflows"):
        if anchor in parts:
            tail = parts[parts.index(anchor):]
            candidate = ROOT.joinpath(*tail)
            if candidate.exists():
                return candidate.resolve()
    return None


def parse_skill_names(identity: str) -> "list[str]":
    """Extract skill names from an identity's 'Load the X, Y, and Z skills.' line."""
    m = re.search(r"Load the (.+?) skills\.", identity)
    if not m:
        return []
    return [n.strip().strip("`") for n in re.split(r",|\band\b", m.group(1)) if n.strip()]


# --------------------------------------------------------------------------- loading
def list_agent_files() -> "list[Path]":
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(p for p in AGENTS_DIR.glob("*.json") if p.is_file())


def agent_names() -> "set[str]":
    return {p.stem for p in list_agent_files()}


def load_agent(name: str, *, strict: bool = True) -> dict:
    """Load an agent config plus its identity prompt.

    strict=True (the default, and what Ultron relies on) exits the process with a
    helpful message on a bad name. strict=False raises BackendError instead, which
    is what the workflow engine wants so one bad stage cannot kill a whole run.
    """
    cfg_path = AGENTS_DIR / f"{name}.json"
    if not cfg_path.is_file():
        available = ", ".join(sorted(agent_names())) or "(none found)"
        _fail(f"unknown agent '{name}'. Available: {available}", 2, strict)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        _fail(f"agent config {cfg_path} is not valid JSON: {e}", 1, strict)
    prompt_uri = cfg.get("prompt", "")
    identity = ""
    if prompt_uri:
        ipath = resolve_uri(prompt_uri)
        if ipath.is_file():
            identity = ipath.read_text(encoding="utf-8").strip()
        else:
            _fail(f"identity prompt not found for '{name}': {ipath}", 1, strict)
    return {
        "name": cfg.get("name", name),
        "description": cfg.get("description", ""),
        "model": cfg.get("model", ""),
        "identity": identity,
        "config": cfg,
    }


def _fail(msg: str, code: int, strict: bool) -> None:
    if strict:
        print(f"alfred: {msg}", file=sys.stderr)
        raise SystemExit(code)
    raise BackendError(msg)


def load_steering() -> str:
    """Concatenate every always-on steering file (Layer 3 - instincts)."""
    if not STEERING_DIR.is_dir():
        return ""
    parts: "list[str]" = []
    for f in sorted(STEERING_DIR.glob("*.md")):
        body, meta = strip_frontmatter(f.read_text(encoding="utf-8"))
        if meta.get("inclusion", "always").lower() != "always":
            continue  # only auto-load the always-on rules, like Kiro does
        parts.append(body.strip())
    return "\n\n".join(parts).strip()


def load_skills(identity: str) -> str:
    """Load the SKILL.md bodies named in the identity's 'Load the ... skills.' line."""
    parts: "list[str]" = []
    for n in parse_skill_names(identity):
        sp = SKILLS_DIR / n / "SKILL.md"
        if sp.is_file():
            body, _ = strip_frontmatter(sp.read_text(encoding="utf-8"))
            parts.append(f"## Skill: {n}\n{body.strip()}")
    return "\n\n".join(parts).strip()


def recall_memory(task: str, k: int = 4) -> str:
    """Best-effort local memory recall via megamind (offline, free). Never fatal."""
    mm = SCRIPTS_DIR / "megamind.py"
    if not mm.is_file() or not task.strip():
        return ""
    try:
        out = subprocess.run(
            [sys.executable, str(mm), "recall", "-q", task, "-k", str(k)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return ""
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("- (")]
    return "\n".join(lines).strip()


# ------------------------------------------------------------------- prompt assembly
DEFAULT_PREAMBLE = (
    "You are operating as an Alfred agent through a non-interactive backend. You have "
    "your usual identity, always-on rules, and skills, but no tool access in this "
    "session - so produce complete text/code answers rather than assuming you can run "
    "tools."
)


def assemble_system_prompt(agent: dict, *, steering: bool, skills: bool,
                           memory_text: str, preamble: str = DEFAULT_PREAMBLE) -> str:
    """Build the full system prompt: preamble + identity + steering + skills + memory."""
    blocks: "list[str]" = []
    if preamble:
        blocks.append(preamble)
    if agent.get("identity"):
        blocks.append(f"# Your identity\n{agent['identity']}")
    if steering:
        s = load_steering()
        if s:
            blocks.append("# Always-on operating rules (Alfred steering)\n" + s)
    if skills:
        sk = load_skills(agent.get("identity", ""))
        if sk:
            blocks.append("# Loaded skills\n" + sk)
    if memory_text:
        blocks.append("# Relevant remembered context (Alfred memory)\n" + memory_text)
    return "\n\n".join(b.strip() for b in blocks if b.strip())


# ------------------------------------------------------------------------ model map
# Kiro model id -> how to ask for the equivalent Claude model on each backend.
#   api    exact Anthropic model id (the API needs the full id).
#   cli    Claude Code alias, so `claude --model opus` always tracks the latest Opus.
#   effort output_config.effort / --effort. xhigh is the sweet spot for coding and
#          agentic work; max is reserved for the ultrathink tier.
MODEL_MAP = {
    "claude-opus-4.8":   {"api": "claude-opus-5",   "cli": "opus",   "effort": "max"},
    "claude-opus-4.6":   {"api": "claude-opus-5",   "cli": "opus",   "effort": "xhigh"},
    "claude-sonnet-4.6": {"api": "claude-sonnet-5", "cli": "sonnet", "effort": "high"},
}
FALLBACK_MODEL = {"api": "claude-opus-5", "cli": "opus", "effort": "high"}

# USD per million tokens (input, output), for run-history cost accounting.
PRICES = {
    "claude-opus-5":   (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def load_model_overrides() -> dict:
    """Optional `.kiro/settings/models.json` overrides, merged over MODEL_MAP.

    Shape mirrors MODEL_MAP: {"claude-opus-4.8": {"api": "...", "cli": "...",
    "effort": "..."}}. Missing keys fall through to the built-in mapping, so an
    override can change just the effort for one tier.
    """
    path = SETTINGS_DIR / "models.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_agent_model(kiro_model: str, backend: str, overrides: "dict | None" = None,
                        model_map: "dict | None" = None) -> "tuple[str, str]":
    """Map a Kiro model id to (model, effort) for `backend`. Pure and testable.

    Unknown ids fall back to the Opus tier rather than erroring - a new agent config
    should still run. `overrides` (from models.json) wins over the built-in map.
    """
    table = dict(model_map if model_map is not None else MODEL_MAP)
    for key, val in (overrides or {}).items():
        merged = dict(table.get(key, FALLBACK_MODEL))
        merged.update(val or {})
        table[key] = merged
    entry = table.get(kiro_model, FALLBACK_MODEL)
    key = "cli" if backend == "claude" else "api"
    return entry.get(key, FALLBACK_MODEL[key]), entry.get("effort", "high")


def estimate_cost(model: str, usage: "dict | None") -> "float | None":
    """USD cost from an Anthropic `usage` block, or None when it can't be priced."""
    if not usage:
        return None
    price = PRICES.get(model)
    if not price:
        return None
    inp = (usage.get("input_tokens", 0) or 0)
    inp += (usage.get("cache_read_input_tokens", 0) or 0) * 0.1
    inp += (usage.get("cache_creation_input_tokens", 0) or 0) * 1.25
    out = usage.get("output_tokens", 0) or 0
    return round(inp / 1e6 * price[0] + out / 1e6 * price[1], 6)


# --------------------------------------------------------------- local (LM Studio)
def endpoint_models(base_url: str, timeout: float = 4.0) -> "list[str] | None":
    """Return the list of loaded model ids, or None if the endpoint is unreachable."""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", [])]
    except Exception:
        return None


def resolve_model(requested: str, models: "list[str] | None", *, quiet: bool = False) -> str:
    """Pick a usable local model: the requested one if loaded, else the first loaded.

    Pure/testable: pass the model list in. Returns `requested` unchanged when the list
    is unknown (None) or already contains it.
    """
    if not models:
        return requested
    if requested in models:
        return requested
    if not quiet:
        print(f"alfred: model '{requested}' not loaded; using '{models[0]}' instead.",
              file=sys.stderr)
    return models[0]


def ensure_local_ready(model: str, base_url: str, *, quiet: bool = False) -> bool:
    """If the local server/model isn't up, try lms-ready.ps1 (Windows). Return readiness."""
    if endpoint_models(base_url) is not None:
        return True
    lms_ready = SCRIPTS_DIR / "lms-ready.ps1"
    if os.name == "nt" and lms_ready.is_file():
        if not quiet:
            print("alfred: local model not up - trying to start LM Studio...", file=sys.stderr)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(lms_ready), "-Model", model, "-BaseUrl", base_url, "-Quiet"],
                timeout=120,
            )
        except Exception:
            pass
    return endpoint_models(base_url) is not None


def _local_request(base_url: str, model: str, messages: "list[dict]", *,
                   temperature: float, max_tokens: int, stream: bool):
    payload = json.dumps({
        "model": model,
        "stream": stream,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode("utf-8")
    return urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )


def call_local(base_url: str, model: str, messages: "list[dict]", *,
               temperature: float = 0.2, max_tokens: int = 1024,
               timeout: int = 300, strict: bool = True) -> str:
    """Non-streaming local completion. Returns the full text."""
    req = _local_request(base_url, model, messages, temperature=temperature,
                         max_tokens=max_tokens, stream=False)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        _fail(f"local model request failed: {e}. Is LM Studio running at {base_url}?",
              3, strict)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        _fail(f"unexpected response from local model: {data}", 1, strict)


def call_local_stream(base_url: str, model: str, messages: "list[dict]", *,
                      temperature: float = 0.2, max_tokens: int = 1024,
                      timeout: int = 300) -> str:
    """Streaming local completion: prints tokens as they arrive, returns the full text.

    Falls back to a non-streaming call if the server does not speak SSE.
    """
    req = _local_request(base_url, model, messages, temperature=temperature,
                         max_tokens=max_tokens, stream=True)
    parts: "list[str]" = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0]["delta"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    print(delta, end="", flush=True)
                    parts.append(delta)
    except urllib.error.URLError as e:
        _fail(f"local model request failed: {e}. Is LM Studio running at {base_url}?", 3, True)
    if parts:
        print()
        return "".join(parts)
    text = call_local(base_url, model, messages, temperature=temperature,
                      max_tokens=max_tokens, timeout=timeout)
    print(text)
    return text


# ------------------------------------------------------------------ Anthropic API
def api_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def call_anthropic(model: str, system: str, task: str, *, max_tokens: int = 16000,
                   effort: "str | None" = None, timeout: "float | None" = 300,
                   fallbacks: bool = True, retries: int = 2,
                   sleeper=time.sleep, opener=None) -> "tuple[str, dict]":
    """POST /v1/messages and return (text, meta). Stdlib-only client.

    Deliberately omits `temperature`/`top_p`/`top_k`: those are REJECTED WITH A 400
    on Opus 5, Sonnet 5, and Opus 4.7/4.8. Depth is controlled with
    `output_config.effort` instead. Thinking is on by default on Opus 5, and
    `max_tokens` caps thinking + visible text together - hence the generous default.

    429 and 5xx are retried with exponential backoff; 4xx is fatal (retrying a
    malformed request just burns time). `opener`/`sleeper` are injected so
    scripts/test_backends.py can exercise this without a network.
    """
    key = api_key()
    if not key:
        raise BackendError("ANTHROPIC_API_KEY is not set; cannot use the 'api' backend.")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": task}],
    }
    if system:
        body["system"] = system
    if effort:
        body["output_config"] = {"effort": effort}

    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if fallbacks:
        # Recommended for Opus 5: a safety-classifier decline is re-served by
        # Anthropic's recommended substitute model inside the same call.
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = FALLBACK_BETA

    url = ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages"
    send = opener or _urlopen_json
    attempt = 0
    while True:
        attempt += 1
        try:
            data = send(url, headers, body, timeout)
            break
        except urllib.error.HTTPError as e:
            status = e.code
            detail = _read_error(e)
            retryable = status == 429 or status >= 500
            if retryable and attempt <= retries + 1 and attempt <= 8:
                delay = min(2.0 ** (attempt - 1), 30.0)
                sleeper(delay)
                continue
            raise BackendError(f"Anthropic API {status}: {detail}")
        except urllib.error.URLError as e:
            if attempt <= retries + 1:
                sleeper(min(2.0 ** (attempt - 1), 30.0))
                continue
            raise BackendError(f"Anthropic API unreachable: {e}")

    stop = data.get("stop_reason")
    served = data.get("model", model)
    meta = {
        "backend": "api",
        "model": served,
        "stop_reason": stop,
        "usage": data.get("usage"),
        "cost_usd": estimate_cost(served, data.get("usage")),
    }
    # Check stop_reason BEFORE reading content: on a refusal, content is empty
    # (pre-output) or a partial to be discarded (mid-stream).
    if stop == "refusal":
        cat = (data.get("stop_details") or {}).get("category")
        meta["refusal_category"] = cat
        return (f"[REFUSAL] The model declined this request"
                f"{f' (category: {cat})' if cat else ''}. Rephrase the task or route "
                f"it to a different agent."), meta
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    if stop == "max_tokens":
        text += ("\n\n[TRUNCATED] Hit max_tokens. On Opus 5 the budget covers thinking "
                 "plus visible text - raise --max-tokens or lower the effort.")
    return text, meta


def _urlopen_json(url: str, headers: dict, body: dict, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(e.read().decode("utf-8"))
        return (payload.get("error") or {}).get("message") or json.dumps(payload)[:400]
    except Exception:
        return e.reason if isinstance(e.reason, str) else str(e)


# ---------------------------------------------------------------- backend probing
BACKENDS = ("claude", "api", "local", "kiro", "dry")
_AUTO_ORDER = ("claude", "api", "local", "kiro")


def have_claude_cli() -> bool:
    """True only if the Claude Code CLI is usable *headlessly* from this repo.

    The CLI being on PATH is not enough: an untrusted workspace makes `claude -p`
    block on the trust dialog, which has no TTY to answer it, so the call hangs
    until its timeout. Treating that as "available" would turn every stage into a
    silent multi-minute stall, so the probe requires trust too.
    """
    return shutil.which("claude") is not None and claude_workspace_trusted()


def claude_workspace_trusted(root: "Path | None" = None) -> bool:
    """Has this workspace been through Claude Code's trust dialog?

    Reads ~/.claude.json (or $CLAUDE_CONFIG_DIR/.claude.json). Absent file or entry
    means untrusted. Never raises - a probe must not be able to break a run.
    """
    target = Path(root or ROOT).resolve()
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    path = (Path(cfg_dir) / ".claude.json") if cfg_dir else (Path.home() / ".claude.json")
    try:
        projects = json.loads(path.read_text(encoding="utf-8")).get("projects", {})
    except Exception:
        return False
    wanted = {str(target), str(target).replace("\\", "/"), target.as_posix()}
    for key, val in projects.items():
        if key in wanted or key.replace("\\", "/") in wanted:
            return bool(isinstance(val, dict) and val.get("hasTrustDialogAccepted"))
    return False


def claude_trust_hint() -> str:
    return (f"the Claude Code CLI is installed, but this workspace is not trusted, so "
            f"`claude -p` would hang on the trust dialog. Fix it once with either:\n"
            f"    cd \"{ROOT}\" && claude        # accept the trust prompt, then /exit\n"
            f"  or set projects[\"{Path(ROOT).as_posix()}\"].hasTrustDialogAccepted = true "
            f"in ~/.claude.json")


def have_kiro_cli() -> bool:
    return shutil.which("kiro-cli") is not None


def have_api_key() -> bool:
    return bool(api_key())


def local_endpoint_up(base_url: str = DEFAULT_LOCAL_ENDPOINT) -> bool:
    return endpoint_models(base_url) is not None


def default_probes() -> dict:
    """Live availability of each backend. Cheap: PATH lookups plus one HTTP HEAD-ish GET."""
    return {
        "claude": have_claude_cli(),
        "api": have_api_key(),
        "local": local_endpoint_up(),
        "kiro": have_kiro_cli(),
        "dry": True,
    }


def resolve_backend(preference: str = "auto", probes: "dict | None" = None) -> str:
    """Pick a backend. Pure when `probes` is supplied, so it is unit-testable.

    'auto' walks claude -> api -> local -> kiro and returns the first available.
    An explicit preference is honoured even if the probe says it is unavailable
    only in the sense that we raise a clear error rather than silently downgrading:
    a silent downgrade from Opus to a 7B local model would be a correctness trap.
    """
    probes = default_probes() if probes is None else probes
    if preference not in ("auto",) + BACKENDS:
        raise BackendError(
            f"unknown backend '{preference}'. Choose from: auto, {', '.join(BACKENDS)}")
    if preference != "auto":
        if not probes.get(preference, False):
            raise BackendError(_unavailable_hint(preference))
        return preference
    for name in _AUTO_ORDER:
        if probes.get(name, False):
            return name
    raise BackendError(
        "no model backend available. Install the Claude Code CLI, set "
        "ANTHROPIC_API_KEY, start LM Studio, or install kiro-cli. "
        "Use --backend dry to preview without running anything.")


def _unavailable_hint(name: str) -> str:
    if name == "claude":
        if shutil.which("claude") is None:
            return "backend 'claude' needs the Claude Code CLI on PATH."
        return "backend 'claude': " + claude_trust_hint()
    return {
        "api": "backend 'api' needs ANTHROPIC_API_KEY in the environment.",
        "local": f"backend 'local' needs LM Studio reachable at {DEFAULT_LOCAL_ENDPOINT}.",
        "kiro": "backend 'kiro' needs kiro-cli on PATH.",
        "dry": "backend 'dry' is always available (this should not happen).",
    }.get(name, f"backend '{name}' is unavailable.")


def backend_report(probes: "dict | None" = None) -> str:
    """Human-readable availability matrix for `doctor` commands."""
    probes = default_probes() if probes is None else probes
    lines = [f"{'backend':<8} {'status':<14} detail"]
    lines.append("-" * 62)
    detail = {
        "claude": "claude -p (tools, subscription)",
        "api": "api.anthropic.com/v1/messages (text-only, portable)",
        "local": f"LM Studio {DEFAULT_LOCAL_ENDPOINT} (free, offline)",
        "kiro": "kiro-cli chat --no-interactive",
        "dry": "echo only; never spawns a model",
    }
    for name in BACKENDS:
        ok = probes.get(name, False)
        lines.append(f"{name:<8} {'available' if ok else 'unavailable':<14} {detail[name]}")
    notes = []
    if not probes.get("claude") and shutil.which("claude") is not None:
        notes.append("claude: " + claude_trust_hint())
    if not probes.get("api"):
        notes.append("api: set ANTHROPIC_API_KEY to enable the portable backend.")
    try:
        chosen = resolve_backend("auto", probes)
    except BackendError as exc:
        chosen = f"(none - {exc})"
    lines.append("")
    lines.append(f"auto would use: {chosen}")
    for note in notes:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------- executors
class Executor:
    """A workflow executor: `(agent, task, timeout=None) -> str`.

    Keeps the plain-string return contract the DAG engine and its tests rely on,
    while exposing per-call metadata (backend, model, cost, stop reason) on
    `.last_meta` so the engine can record it in run history.

    `last_meta` is thread-local: the engine runs a wave's stages concurrently
    through one shared Executor, so a plain attribute would let one stage's
    metadata land on another stage's record.
    """

    def __init__(self, fn, backend: str, label: "str | None" = None):
        self._fn = fn
        self.backend = backend
        self.label = label or backend
        self._local = threading.local()

    @property
    def last_meta(self) -> dict:
        return getattr(self._local, "meta", {})

    def __call__(self, agent: str, task: str, timeout=None) -> str:
        text, meta = self._fn(agent, task, timeout)
        meta.setdefault("backend", self.backend)
        self._local.meta = meta
        return text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Executor {self.label}>"


def echo_executor(agent, task, timeout=None):
    """Dry executor: describes what WOULD run. Never spawns an agent."""
    preview = task if len(task) <= 400 else task[:400] + " ...[truncated]"
    return f"[DRY-RUN] would run agent '{agent}' with task:\n{preview}"


def kiro_executor(agent, task, timeout=None):
    """Live executor: runs a stage via `kiro-cli chat --no-interactive`."""
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools",
           "--agent", agent, task]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                              timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise BackendError("kiro-cli not found on PATH; use --backend dry to preview")
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] stage exceeded {timeout}s and was terminated."
    if proc.returncode != 0:
        return (proc.stdout or "") + "\n[stderr]\n" + (proc.stderr or "")
    return proc.stdout or ""


def _claude_call(agent: str, task: str, timeout, *, model: str, effort: str,
                 permission_mode: str, max_budget_usd: "float | None",
                 fallback_model: "str | None", extra_args: "list[str]"):
    """Run one stage through the Claude Code CLI in headless (-p) mode."""
    cmd = ["claude", "-p", "--output-format", "json",
           "--model", model, "--effort", effort,
           "--permission-mode", permission_mode,
           "--add-dir", str(ROOT)]
    # `--agent` resolves the identity/tools/model from .claude/agents/<name>.md,
    # which scripts/sync-claude-config.py generates from .kiro/. Before the first
    # sync (or for an agent that has no generated file) fall back to injecting the
    # assembled Alfred system prompt directly, so nothing silently runs bare.
    if (CLAUDE_AGENTS_DIR / f"{agent}.md").is_file():
        cmd += ["--agent", agent]
    else:
        try:
            cfg = load_agent(agent, strict=False)
            system = assemble_system_prompt(cfg, steering=True, skills=False,
                                            memory_text="")
        except BackendError:
            system = ""
        if system:
            cmd += ["--append-system-prompt", system]
    if max_budget_usd:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    if fallback_model:
        cmd += ["--fallback-model", fallback_model]
    cmd += extra_args

    try:
        # The rendered task carries every dependency's output and can be far longer
        # than a Windows command line allows, so it goes in on stdin, not argv.
        proc = subprocess.run(cmd, input=task, capture_output=True, text=True,
                              cwd=str(ROOT), timeout=timeout,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise BackendError("claude CLI not found on PATH; install Claude Code or "
                           "use --backend api/local/dry")
    except subprocess.TimeoutExpired:
        return (f"[TIMEOUT] stage exceeded {timeout}s and was terminated.",
                {"model": model, "status": "timeout"})

    meta = {"model": model, "effort": effort, "returncode": proc.returncode}
    raw = (proc.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        if proc.returncode != 0:
            return (raw + "\n[stderr]\n" + (proc.stderr or ""), meta)
        return (raw, meta)  # not JSON but succeeded - hand back what we got
    meta["cost_usd"] = payload.get("total_cost_usd")
    meta["num_turns"] = payload.get("num_turns")
    meta["session_id"] = payload.get("session_id")
    text = payload.get("result") or ""
    if payload.get("is_error"):
        text = f"[ERROR] {text or payload.get('subtype') or 'claude reported an error'}"
    return text, meta


def _api_call(agent: str, task: str, timeout, *, overrides: dict, max_tokens: int,
              effort_override: "str | None", model_override: "str | None",
              fallbacks: bool, skills: bool):
    cfg = load_agent(agent, strict=False)
    model, effort = resolve_agent_model(cfg.get("model", ""), "api", overrides)
    system = assemble_system_prompt(cfg, steering=True, skills=skills, memory_text="")
    return call_anthropic(
        model_override or model, system, task,
        max_tokens=max_tokens, effort=effort_override or effort,
        timeout=timeout, fallbacks=fallbacks)


def _local_call(agent: str, task: str, timeout, *, endpoint: str, model: str,
                max_tokens: int, temperature: float, skills: bool):
    cfg = load_agent(agent, strict=False)
    system = assemble_system_prompt(cfg, steering=True, skills=skills, memory_text="")
    if not ensure_local_ready(model, endpoint, quiet=True):
        raise BackendError(f"local model not reachable at {endpoint}")
    resolved = resolve_model(model, endpoint_models(endpoint), quiet=True)
    text = call_local(endpoint, resolved,
                      [{"role": "system", "content": system},
                       {"role": "user", "content": task}],
                      temperature=temperature, max_tokens=max_tokens,
                      timeout=int(timeout or 300), strict=False)
    return text, {"model": resolved, "cost_usd": 0.0}


def make_executor(backend: str = "auto", *, probes: "dict | None" = None,
                  model: "str | None" = None, effort: "str | None" = None,
                  max_tokens: int = 16000, permission_mode: str = "acceptEdits",
                  max_budget_usd: "float | None" = None,
                  fallback_model: "str | None" = None,
                  fallbacks: "bool | None" = None,
                  endpoint: str = DEFAULT_LOCAL_ENDPOINT,
                  local_model: str = DEFAULT_LOCAL_MODEL,
                  temperature: float = 0.2, skills: bool = False,
                  extra_args: "list[str] | None" = None) -> Executor:
    """Build an Executor for `backend` ('auto' resolves one). Never spends on 'dry'."""
    chosen = resolve_backend(backend, probes)
    overrides = load_model_overrides()
    if fallbacks is None:
        fallbacks = os.environ.get("ALFRED_API_FALLBACKS", "1") not in ("0", "false", "no")

    if chosen == "dry":
        return Executor(lambda a, t, to: (echo_executor(a, t, to), {"model": "(dry)"}),
                        "dry")
    if chosen == "kiro":
        return Executor(lambda a, t, to: (kiro_executor(a, t, to), {"model": "(kiro)"}),
                        "kiro")
    if chosen == "claude":
        def run(a, t, to):
            cfg_model, cfg_effort = FALLBACK_MODEL["cli"], FALLBACK_MODEL["effort"]
            try:
                cfg = load_agent(a, strict=False)
                cfg_model, cfg_effort = resolve_agent_model(
                    cfg.get("model", ""), "claude", overrides)
            except BackendError:
                pass
            return _claude_call(
                a, t, to, model=model or cfg_model, effort=effort or cfg_effort,
                permission_mode=permission_mode, max_budget_usd=max_budget_usd,
                fallback_model=fallback_model, extra_args=list(extra_args or []))
        return Executor(run, "claude")
    if chosen == "api":
        return Executor(
            lambda a, t, to: _api_call(
                a, t, to, overrides=overrides, max_tokens=max_tokens,
                effort_override=effort, model_override=model,
                fallbacks=bool(fallbacks), skills=skills),
            "api")
    if chosen == "local":
        return Executor(
            lambda a, t, to: _local_call(
                a, t, to, endpoint=endpoint, model=model or local_model,
                max_tokens=max_tokens, temperature=temperature, skills=skills),
            "local")
    raise BackendError(f"unhandled backend '{chosen}'")  # pragma: no cover
