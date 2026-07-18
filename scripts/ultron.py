#!/usr/bin/env python3
"""Ultron - Alfred's local CLI (a Kiro-compatible agent front end).

Runs the SAME Alfred agents (identity + always-on steering + skills + memory) that
`kiro-cli chat --agent <name>` would, but against the FREE local model (Qwen2.5-Coder via
LM Studio's OpenAI-compatible API) instead of spending Kiro/Opus credits. When Kiro is
available again, `--backend kiro` transparently hands the same task to `kiro-cli`.

Design goals:
  - Zero third-party dependencies (Python stdlib only; works fully offline).
  - Reuse the existing brain layers: .kiro/agents/<name>.json -> identity.txt,
    .kiro/steering/*.md (always-on), .kiro/skills/<name>/SKILL.md, and the megamind memory.
  - Never call a premium/cloud endpoint on the local backend - it only talks to localhost.

Usage:
  python scripts/ultron.py agents                       # list available agents
  python scripts/ultron.py run --agent alfred-qa "..."  # one-shot task (local model)
  python scripts/ultron.py chat --agent alfred-coder    # interactive session
  python scripts/ultron.py run --agent alfred-qa --dry-run "..."   # show the assembled
                                                        # prompt without calling a model
  python scripts/ultron.py run --agent alfred-coder --backend kiro "..."  # use Kiro later

Env overrides: ULTRON_MODEL, ULTRON_ENDPOINT.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".kiro" / "agents"
STEERING_DIR = ROOT / ".kiro" / "steering"
SKILLS_DIR = ROOT / ".kiro" / "skills"
SCRIPTS_DIR = ROOT / "scripts"

DEFAULT_MODEL = os.environ.get("ULTRON_MODEL", "alfred-coder-7b")
DEFAULT_ENDPOINT = os.environ.get("ULTRON_ENDPOINT", "http://localhost:1234/v1")

# Make Windows consoles tolerate the em-dashes/box glyphs in steering + skills.
try:  # pragma: no cover - depends on the console
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- helpers
def strip_frontmatter(text: str) -> tuple[str, dict]:
    """Split leading YAML-ish frontmatter (--- ... ---) from a markdown body.

    Returns (body, meta) where meta holds simple `key: value` pairs found in the block.
    """
    meta: dict[str, str] = {}
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
    """Resolve a `file:///C:/Alfred/...` URI (or a plain/relative path) to a Path."""
    if uri.startswith("file:///"):
        p = uri[len("file:///"):]
    elif uri.startswith("file://"):
        p = uri[len("file://"):]
    else:
        p = uri
    path = Path(p)
    if not path.is_absolute():
        path = (ROOT / p).resolve()
    return path


def die(msg: str, code: int = 1) -> "None":
    print(f"ultron: {msg}", file=sys.stderr)
    raise SystemExit(code)


# --------------------------------------------------------------------------- loading
def list_agent_files() -> list[Path]:
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(p for p in AGENTS_DIR.glob("*.json") if p.is_file())


def load_agent(name: str) -> dict:
    """Load an agent config + its identity prompt. Raises SystemExit on failure."""
    cfg_path = AGENTS_DIR / f"{name}.json"
    if not cfg_path.is_file():
        available = ", ".join(p.stem for p in list_agent_files()) or "(none found)"
        die(f"unknown agent '{name}'. Available: {available}", 2)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"agent config {cfg_path} is not valid JSON: {e}")
    prompt_uri = cfg.get("prompt", "")
    identity = ""
    if prompt_uri:
        ipath = resolve_uri(prompt_uri)
        if ipath.is_file():
            identity = ipath.read_text(encoding="utf-8").strip()
        else:
            die(f"identity prompt not found for '{name}': {ipath}")
    return {
        "name": cfg.get("name", name),
        "description": cfg.get("description", ""),
        "model": cfg.get("model", ""),
        "identity": identity,
        "config": cfg,
    }


def load_steering() -> str:
    """Concatenate every always-on steering file (Layer 3 - instincts)."""
    if not STEERING_DIR.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(STEERING_DIR.glob("*.md")):
        body, meta = strip_frontmatter(f.read_text(encoding="utf-8"))
        if meta.get("inclusion", "always").lower() != "always":
            continue  # only auto-load the always-on rules, like Kiro does
        parts.append(body.strip())
    return "\n\n".join(parts).strip()


def load_skills(identity: str) -> str:
    """Load the SKILL.md bodies named in the identity's 'Load the X, Y skills.' line."""
    m = re.search(r"Load the (.+?) skills\.", identity)
    if not m:
        return ""
    raw = m.group(1)
    names = [n.strip() for n in re.split(r",|\band\b", raw) if n.strip()]
    parts: list[str] = []
    for n in names:
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
def assemble_system_prompt(agent: dict, *, steering: bool, skills: bool,
                           memory_text: str) -> str:
    blocks: list[str] = []
    blocks.append(
        "You are operating inside Ultron, Alfred's local CLI. It mirrors the Kiro "
        "agent-chat workflow: you have the same identity, always-on rules, and skills, "
        "but you are running on a local model with no tool access in this session - so "
        "produce complete text/code answers rather than assuming you can run tools."
    )
    if agent["identity"]:
        blocks.append(f"# Your identity\n{agent['identity']}")
    if steering:
        s = load_steering()
        if s:
            blocks.append("# Always-on operating rules (Alfred steering)\n" + s)
    if skills:
        sk = load_skills(agent["identity"])
        if sk:
            blocks.append("# Loaded skills\n" + sk)
    if memory_text:
        blocks.append("# Relevant remembered context (Alfred memory)\n" + memory_text)
    return "\n\n".join(b.strip() for b in blocks if b.strip())


# ------------------------------------------------------------------------- backends
def endpoint_models(base_url: str, timeout: float = 4.0) -> list[str] | None:
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", [])]
    except Exception:
        return None


def ensure_local_ready(model: str, base_url: str) -> bool:
    """If the local server/model isn't up, try lms-ready.ps1 (Windows). Return readiness."""
    if endpoint_models(base_url) is not None:
        return True
    lms_ready = SCRIPTS_DIR / "lms-ready.ps1"
    if os.name == "nt" and lms_ready.is_file():
        print("ultron: local model not up - trying to start LM Studio...", file=sys.stderr)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(lms_ready), "-Model", model, "-BaseUrl", base_url, "-Quiet"],
                timeout=90,
            )
        except Exception:
            pass
    return endpoint_models(base_url) is not None


def call_local(base_url: str, model: str, messages: list[dict], *,
               temperature: float, max_tokens: int, timeout: int) -> str:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        die(f"local model request failed: {e}. Is LM Studio running at {base_url}?", 3)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        die(f"unexpected response from local model: {data}")


def run_kiro(name: str, task: str) -> int:
    """Passthrough to kiro-cli (the 'later, with credits' path)."""
    exe = "kiro-cli"
    cmd = [exe, "chat", "--agent", name]
    if task:
        cmd.append(task)
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        die("kiro-cli not found on PATH. Install Kiro or use the default local backend.", 127)


# --------------------------------------------------------------------------- commands
def cmd_agents(_: argparse.Namespace) -> int:
    files = list_agent_files()
    if not files:
        die(f"no agents found in {AGENTS_DIR}", 2)
    print(f"Ultron - {len(files)} Alfred agents available:\n")
    for f in files:
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  {f.stem:<24} (invalid JSON)")
            continue
        desc = cfg.get("description", "").strip()
        if len(desc) > 96:
            desc = desc[:93] + "..."
        print(f"  {f.stem:<24} {desc}")
    print('\nRun one:  ultron run --agent <name> "your task"    (add --dry-run to preview)')
    return 0


def _build_messages(agent: dict, task: str, args: argparse.Namespace) -> list[dict]:
    memory_text = "" if args.no_memory else recall_memory(task)
    system = assemble_system_prompt(
        agent, steering=not args.no_steering, skills=args.skills, memory_text=memory_text
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]


def cmd_run(args: argparse.Namespace) -> int:
    task = " ".join(args.task).strip()
    if not task and not args.dry_run:
        die('no task given. Example: ultron run --agent alfred-qa "draft a test plan"', 2)

    if args.backend == "kiro":
        return run_kiro(args.agent, task)

    agent = load_agent(args.agent)
    messages = _build_messages(agent, task, args)

    if args.dry_run:
        print(f"# Ultron dry-run\n# agent    : {agent['name']}")
        print(f"# backend  : {args.backend}\n# model    : {args.model}")
        print(f"# endpoint : {args.endpoint}")
        print(f"# kiro-model (for reference): {agent['model']}")
        print("\n===== SYSTEM PROMPT =====\n")
        print(messages[0]["content"])
        print("\n===== USER MESSAGE =====\n")
        print(messages[1]["content"] or "(empty)")
        return 0

    if not ensure_local_ready(args.model, args.endpoint):
        die(f"local model not reachable at {args.endpoint} and LM Studio could not be "
            f"started. Start it (lms server start) or use --dry-run / --backend kiro.", 3)

    reply = call_local(
        args.endpoint, args.model, messages,
        temperature=args.temperature, max_tokens=args.max_tokens, timeout=args.timeout,
    )
    print(reply)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    if args.backend == "kiro":
        return run_kiro(args.agent, "")

    agent = load_agent(args.agent)
    if not ensure_local_ready(args.model, args.endpoint):
        die(f"local model not reachable at {args.endpoint}. Start LM Studio first.", 3)

    system = assemble_system_prompt(
        agent, steering=not args.no_steering, skills=args.skills, memory_text=""
    )
    history: list[dict] = [{"role": "system", "content": system}]
    print(f"Ultron - chatting with {agent['name']} on {args.model} (local). "
          "Type /exit to quit, /reset to clear history.\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            break
        if user == "/reset":
            history = [{"role": "system", "content": system}]
            print("(history cleared)\n")
            continue
        mem = "" if args.no_memory else recall_memory(user)
        content = user if not mem else f"Relevant memory:\n{mem}\n\nUser: {user}"
        history.append({"role": "user", "content": content})
        reply = call_local(
            args.endpoint, args.model, history,
            temperature=args.temperature, max_tokens=args.max_tokens, timeout=args.timeout,
        )
        # Store the clean user text (not the memory-augmented copy) in history.
        history[-1] = {"role": "user", "content": user}
        history.append({"role": "assistant", "content": reply})
        print(f"\n{agent['name']}> {reply}\n")
    return 0


# ------------------------------------------------------------------------------ cli
def add_model_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--agent", "-a", required=True, help="agent name (see 'ultron agents')")
    sp.add_argument("--backend", choices=["local", "kiro"], default="local",
                    help="local = LM Studio (free, default); kiro = kiro-cli passthrough")
    sp.add_argument("--model", default=DEFAULT_MODEL, help=f"local model id (default {DEFAULT_MODEL})")
    sp.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="OpenAI-compatible base URL")
    sp.add_argument("--max-tokens", type=int, default=1024, dest="max_tokens")
    sp.add_argument("--temperature", type=float, default=0.2)
    sp.add_argument("--timeout", type=int, default=300, help="HTTP timeout seconds (CPU is slow)")
    sp.add_argument("--no-steering", action="store_true", help="omit the always-on steering rules")
    sp.add_argument("--skills", action="store_true", help="also load the agent's SKILL.md files")
    sp.add_argument("--no-memory", action="store_true", help="skip local memory recall")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ultron",
        description="Ultron - Alfred's local CLI (Kiro-compatible agent workflow, free/offline).",
    )
    p.add_argument("--version", action="version", version=f"ultron {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("agents", help="list available Alfred agents").set_defaults(func=cmd_agents)

    r = sub.add_parser("run", help="run a one-shot task with an agent")
    add_model_flags(r)
    r.add_argument("--dry-run", action="store_true",
                   help="assemble and print the prompt without calling a model")
    r.add_argument("task", nargs="*", help="the task/question (quote it)")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("chat", help="interactive chat with an agent")
    add_model_flags(c)
    c.set_defaults(func=cmd_chat)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
