#!/usr/bin/env python3
"""Ultron - Alfred's local CLI (a Kiro-compatible agent front end).

Runs the SAME Alfred agents (identity + always-on steering + skills + memory) that
`kiro-cli chat --agent <name>` would, against whichever model backend you pick:

  local   the FREE local model (Qwen2.5-Coder via LM Studio) - the default, so a
          stray command never spends credits
  claude  the Claude Code CLI (`claude -p`), with real tools on this PC
  api     the Anthropic API directly - works from any machine with a key
  kiro    passthrough to `kiro-cli`

Design goals:
  - Zero third-party dependencies (Python stdlib only; the local path works offline).
  - Reuse the existing brain layers via scripts/backends.py: .kiro/agents/<name>.json
    -> identity.txt, .kiro/steering/*.md (always-on), .kiro/skills/<name>/SKILL.md,
    and the megamind memory.
  - The local backend only ever talks to localhost - it can never reach a paid endpoint.

Commands:
  ultron agents                          # list available agents
  ultron doctor                          # health check (every backend + agent configs)
  ultron run --agent alfred-qa "..."     # one-shot task (local model)
  ultron chat --agent alfred-coder       # interactive session (streams by default)
  ultron run --agent alfred-qa --dry-run "..."            # preview the assembled prompt
  ultron run --agent alfred-coder --backend claude "..."  # real tools, via Claude Code
  ultron run --agent alfred-qa --backend api "..."        # Anthropic API, from anywhere

Env overrides: ULTRON_MODEL, ULTRON_ENDPOINT, ANTHROPIC_API_KEY, ALFRED_ROOT.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backends as B  # noqa: E402

__version__ = "0.3.0"

ROOT = B.ROOT
AGENTS_DIR = B.AGENTS_DIR
STEERING_DIR = B.STEERING_DIR
SKILLS_DIR = B.SKILLS_DIR
SCRIPTS_DIR = B.SCRIPTS_DIR

DEFAULT_MODEL = B.DEFAULT_LOCAL_MODEL
DEFAULT_ENDPOINT = B.DEFAULT_LOCAL_ENDPOINT

# Shared brain helpers - one implementation, in backends.py.
strip_frontmatter = B.strip_frontmatter
resolve_uri = B.resolve_uri
parse_skill_names = B.parse_skill_names
list_agent_files = B.list_agent_files
load_agent = B.load_agent
load_steering = B.load_steering
load_skills = B.load_skills
recall_memory = B.recall_memory
endpoint_models = B.endpoint_models
resolve_model = B.resolve_model
ensure_local_ready = B.ensure_local_ready
call_local = B.call_local
call_local_stream = B.call_local_stream

ULTRON_PREAMBLE = (
    "You are operating inside Ultron, Alfred's local CLI. It mirrors the Kiro "
    "agent-chat workflow: you have the same identity, always-on rules, and skills, "
    "but you are running on a local model with no tool access in this session - so "
    "produce complete text/code answers rather than assuming you can run tools."
)

# Make Windows consoles tolerate the em-dashes/box glyphs in steering + skills.
try:  # pragma: no cover - depends on the console
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def die(msg: str, code: int = 1) -> None:
    print(f"ultron: {msg}", file=sys.stderr)
    raise SystemExit(code)


def assemble_system_prompt(agent: dict, *, steering: bool, skills: bool,
                           memory_text: str, preamble: str = ULTRON_PREAMBLE) -> str:
    """Ultron's system prompt: the shared assembly with Ultron's own preamble."""
    return B.assemble_system_prompt(agent, steering=steering, skills=skills,
                                    memory_text=memory_text, preamble=preamble)


def run_kiro(name: str, task: str) -> int:
    """Passthrough to kiro-cli (the 'later, with credits' path)."""
    import subprocess
    cmd = ["kiro-cli", "chat", "--agent", name]
    if task:
        cmd.append(task)
    try:
        return subprocess.run(cmd).returncode
    except FileNotFoundError:
        die("kiro-cli not found on PATH. Install Kiro or use the default local backend.",
            127)


def run_claude_interactive(name: str) -> int:
    """Hand an interactive session to the Claude Code CLI with the Alfred agent."""
    import subprocess
    try:
        return subprocess.run(["claude", "--agent", name]).returncode
    except FileNotFoundError:
        die("claude CLI not found on PATH. Install Claude Code or use --backend local.",
            127)


# --------------------------------------------------------------------------- commands
def cmd_agents(_: argparse.Namespace) -> int:
    files = list_agent_files()
    if not files:
        die(f"no agents found in {AGENTS_DIR}", 2)
    print(f"Ultron - {len(files)} Alfred agents available:\n")
    for f in files:
        try:
            cfg = json.loads(f.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            print(f"  {f.stem:<24} (invalid JSON)")
            continue
        desc = cfg.get("description", "").strip()
        if len(desc) > 96:
            desc = desc[:93] + "..."
        print(f"  {f.stem:<24} {desc}")
    print('\nRun one:  ultron run --agent <name> "your task"    (add --dry-run to preview)')
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Health check: every backend, the agent configs, memory, and steering."""
    print(f"Ultron {__version__} - doctor\n")
    print(f"  repo root : {ROOT}")
    print(f"  python    : {sys.version.split()[0]}")
    print(f"  endpoint  : {args.endpoint}")
    models = endpoint_models(args.endpoint)
    if models is None:
        print("  LM Studio : NOT reachable  (start it with `lms server start`, or just run")
        print("              `ultron run ...` which auto-starts it via scripts/lms-ready.ps1)")
    else:
        loaded = ", ".join(m for m in models if m) or "(none loaded)"
        print(f"  LM Studio : reachable - loaded models: {loaded}")

    files = list_agent_files()
    problems: "list[str]" = []
    for f in files:
        try:
            cfg = json.loads(f.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            problems.append(f"{f.stem}: invalid JSON ({e})")
            continue
        ipath = resolve_uri(cfg.get("prompt", ""))
        if not ipath.is_file():
            problems.append(f"{f.stem}: identity prompt missing ({ipath})")
    print(f"  agents    : {len(files)} configs in {AGENTS_DIR}")
    if problems:
        print(f"  config    : {len(problems)} issue(s):")
        for p in problems:
            print(f"      - {p}")
    else:
        print("  config    : OK - all configs parse and their identity files exist")

    mm = SCRIPTS_DIR / "megamind.py"
    print(f"  memory    : {'megamind.py present' if mm.is_file() else 'megamind.py MISSING'}")
    steering_n = len(list(STEERING_DIR.glob('*.md'))) if STEERING_DIR.is_dir() else 0
    print(f"  steering  : {steering_n} rule file(s) in {STEERING_DIR}")
    claude_dir = ROOT / ".claude" / "agents"
    n_claude = len(list(claude_dir.glob("*.md"))) if claude_dir.is_dir() else 0
    print(f"  .claude/  : {n_claude} generated agent file(s)"
          f"{'' if n_claude else '  - run scripts/sync-claude-config.py'}")

    print("\n  backends:")
    for line in B.backend_report().splitlines():
        print(f"    {line}")

    print()
    if problems:
        print("  RESULT: issues found (see above).")
        return 1
    print("  RESULT: healthy." if models is not None
          else "  RESULT: configs healthy; local model offline "
               "(start LM Studio, or use --backend claude/api).")
    return 0


def _build_messages(agent: dict, task: str, args: argparse.Namespace) -> "list[dict]":
    memory_text = "" if args.no_memory else recall_memory(task)
    system = assemble_system_prompt(
        agent, steering=not args.no_steering, skills=args.skills, memory_text=memory_text
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]


def _generate(args: argparse.Namespace, model: str, messages: "list[dict]", *,
              stream: bool) -> str:
    if stream:
        return call_local_stream(args.endpoint, model, messages,
                                 temperature=args.temperature, max_tokens=args.max_tokens,
                                 timeout=args.timeout)
    text = call_local(args.endpoint, model, messages,
                      temperature=args.temperature, max_tokens=args.max_tokens,
                      timeout=args.timeout)
    print(text)
    return text


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
        if args.backend in ("claude", "api"):
            resolved, effort = B.resolve_agent_model(
                agent["model"], args.backend, B.load_model_overrides())
            print(f"# resolved : {resolved} (effort {effort})")
        print("\n===== SYSTEM PROMPT =====\n")
        print(messages[0]["content"])
        print("\n===== USER MESSAGE =====\n")
        print(messages[1]["content"] or "(empty)")
        return 0

    if args.backend in ("claude", "api"):
        # Both go through the shared executors, so Ultron and the DAG engine
        # produce byte-identical prompts and identical model routing.
        try:
            ex = B.make_executor(args.backend, model=args.model_override,
                                 max_tokens=args.max_tokens, skills=args.skills)
        except B.BackendError as exc:
            die(str(exc), 3)
        text = ex(args.agent, task, timeout=args.timeout)
        print(text)
        meta = ex.last_meta
        if not args.quiet and meta:
            cost = meta.get("cost_usd")
            print(f"\n[{meta.get('backend')} - {meta.get('model')}"
                  f"{f' - ${cost:.4f}' if cost else ''}]", file=sys.stderr)
        return 0

    if not ensure_local_ready(args.model, args.endpoint, quiet=args.quiet):
        die(f"local model not reachable at {args.endpoint} and LM Studio could not be "
            f"started. Start it (lms server start), or use --dry-run / "
            f"--backend claude / --backend api.", 3)
    model = resolve_model(args.model, endpoint_models(args.endpoint), quiet=args.quiet)
    _generate(args, model, messages, stream=args.stream)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    if args.backend == "kiro":
        return run_kiro(args.agent, "")
    if args.backend == "claude":
        # Claude Code already has a first-class interactive session; use it, with
        # the generated Alfred agent selected.
        return run_claude_interactive(args.agent)

    agent = load_agent(args.agent)

    if args.backend == "api":
        return _chat_api(agent, args)

    if not ensure_local_ready(args.model, args.endpoint, quiet=args.quiet):
        die(f"local model not reachable at {args.endpoint}. Start LM Studio first.", 3)
    model = resolve_model(args.model, endpoint_models(args.endpoint), quiet=args.quiet)

    system = assemble_system_prompt(
        agent, steering=not args.no_steering, skills=args.skills, memory_text=""
    )
    history: "list[dict]" = [{"role": "system", "content": system}]
    stream = not args.no_stream
    print(f"Ultron - chatting with {agent['name']} on {model} (local). "
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
        print(f"\n{agent['name']}> ", end="", flush=True)
        if stream:
            reply = call_local_stream(args.endpoint, model, history,
                                      temperature=args.temperature,
                                      max_tokens=args.max_tokens, timeout=args.timeout)
        else:
            reply = call_local(args.endpoint, model, history,
                               temperature=args.temperature,
                               max_tokens=args.max_tokens, timeout=args.timeout)
            print(reply)
        # Store the clean user text (not the memory-augmented copy) in history.
        history[-1] = {"role": "user", "content": user}
        history.append({"role": "assistant", "content": reply})
        print()
    return 0


def _chat_api(agent: dict, args: argparse.Namespace) -> int:
    """Multi-turn chat against the Anthropic API (text-only; no tools)."""
    model, effort = B.resolve_agent_model(agent["model"], "api", B.load_model_overrides())
    model = args.model_override or model
    system = assemble_system_prompt(agent, steering=not args.no_steering,
                                    skills=args.skills, memory_text="")
    transcript: "list[str]" = []
    print(f"Ultron - chatting with {agent['name']} on {model} (Anthropic API, no tools). "
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
            transcript.clear()
            print("(history cleared)\n")
            continue
        # /v1/messages is stateless, so replay the transcript as one user turn.
        convo = "\n\n".join(transcript + [f"User: {user}"])
        try:
            text, meta = B.call_anthropic(model, system, convo,
                                          max_tokens=args.max_tokens, effort=effort,
                                          timeout=args.timeout)
        except B.BackendError as exc:
            print(f"\nultron: {exc}\n", file=sys.stderr)
            continue
        print(f"\n{agent['name']}> {text}\n")
        if not args.quiet and meta.get("cost_usd"):
            print(f"[${meta['cost_usd']:.4f}]", file=sys.stderr)
        transcript += [f"User: {user}", f"Assistant: {text}"]
    return 0


# ------------------------------------------------------------------------------ cli
def add_model_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--agent", "-a", required=True, help="agent name (see 'ultron agents')")
    sp.add_argument("--backend", choices=["local", "claude", "api", "kiro"],
                    default="local",
                    help="local = LM Studio (free, default); claude = Claude Code CLI "
                         "(tools); api = Anthropic API (portable); kiro = kiro-cli")
    sp.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"local model id (default {DEFAULT_MODEL})")
    sp.add_argument("--model-override", default=None, dest="model_override",
                    help="claude/api: override the model this agent maps to")
    sp.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="OpenAI-compatible base URL")
    sp.add_argument("--max-tokens", type=int, default=1024, dest="max_tokens")
    sp.add_argument("--temperature", type=float, default=0.2,
                    help="local backend only (the Claude models reject sampling params)")
    sp.add_argument("--timeout", type=int, default=300, help="HTTP timeout seconds (CPU is slow)")
    sp.add_argument("--no-steering", action="store_true", help="omit the always-on steering rules")
    sp.add_argument("--skills", action="store_true", help="also load the agent's SKILL.md files")
    sp.add_argument("--no-memory", action="store_true", help="skip local memory recall")
    sp.add_argument("--quiet", action="store_true", help="suppress ultron status notes on stderr")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ultron",
        description="Ultron - Alfred's local CLI (Kiro-compatible agent workflow, "
                    "free/offline by default).",
    )
    p.add_argument("--version", action="version", version=f"ultron {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("agents", help="list available Alfred agents").set_defaults(func=cmd_agents)

    d = sub.add_parser("doctor", help="health check: backends, models, agent configs")
    d.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="OpenAI-compatible base URL")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("run", help="run a one-shot task with an agent")
    add_model_flags(r)
    r.add_argument("--stream", action="store_true", help="stream tokens as they generate")
    r.add_argument("--dry-run", action="store_true",
                   help="assemble and print the prompt without calling a model")
    r.add_argument("task", nargs="*", help="the task/question (quote it)")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("chat", help="interactive chat with an agent")
    add_model_flags(c)
    c.add_argument("--no-stream", action="store_true", help="disable streaming (print once)")
    c.set_defaults(func=cmd_chat)
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
