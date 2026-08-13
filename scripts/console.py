#!/usr/bin/env python3
"""Alfred console - one interactive terminal surface for the whole system.

WHY THIS EXISTS
---------------
Alfred grew a lot of good machinery behind a lot of separate commands: the harness,
the gauntlet engine, the memory graph, the local model, Ultron, the dashboard. Each
needs a different invocation and none of them shows you the system. This is the
single place you drive it from.

The graph runner is rendered as **motion**: a chain of nodes redrawn in place as
control moves through it, with gate verdicts and forced routes appearing on the
edges. That is deliberate - the engine is a graph with explicit gates, not a retry
loop, and the display should show that rather than a scrolling log.

DESIGN CONSTRAINTS
------------------
* **Zero dependencies.** Standard library only. No curses (absent on Windows), no
  rich, no textual. ANSI escapes directly, with a plain-text fallback.
* **Encoding-safe.** The Windows console defaults to cp1252 and raises
  UnicodeEncodeError on box-drawing glyphs. Every glyph has an ASCII fallback and
  the set is chosen once at startup from the real stdout encoding.
* **Never blocks silently.** Long operations animate; if something is unreachable
  it says so instead of hanging.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

LMSTUDIO = os.environ.get("ALFRED_LMSTUDIO", "http://localhost:1234")


# --------------------------------------------------------------------- terminal


def _enable_ansi() -> bool:
    """Turn on VT processing so ANSI escapes work in the Windows console."""
    if os.name != "nt":
        return sys.stdout.isatty()
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:  # noqa: BLE001 - a dumb terminal is a valid state
        return False


ANSI = _enable_ansi() and not os.environ.get("NO_COLOR")


def _supports_unicode() -> bool:
    """Only use box-drawing glyphs if stdout can actually encode them."""
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in encoding:
        return True
    # Try to upgrade the stream; Windows Terminal handles UTF-8 fine.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


UNICODE = _supports_unicode()


class C:
    """Colours. Empty strings when ANSI is unavailable, so output stays clean."""

    reset = "\x1b[0m" if ANSI else ""
    bold = "\x1b[1m" if ANSI else ""
    dim = "\x1b[2m" if ANSI else ""
    red = "\x1b[91m" if ANSI else ""
    green = "\x1b[92m" if ANSI else ""
    yellow = "\x1b[93m" if ANSI else ""
    blue = "\x1b[94m" if ANSI else ""
    magenta = "\x1b[95m" if ANSI else ""
    cyan = "\x1b[96m" if ANSI else ""
    grey = "\x1b[90m" if ANSI else ""


G = {
    "work": "*" if not UNICODE else "\u25cf",        # ●
    "gate": "<>" if not UNICODE else "\u25c6",       # ◆
    "approval": "!" if not UNICODE else "\u25b2",    # ▲
    "pipe": "|" if not UNICODE else "\u2502",        # │
    "tee": "+-" if not UNICODE else "\u251c\u2500",  # ├─
    "end": "\\-" if not UNICODE else "\u2570\u2500", # ╰─
    "arrow": "->" if not UNICODE else "\u2192",      # →
    "ok": "OK" if not UNICODE else "\u2713",         # ✓
    "fail": "X" if not UNICODE else "\u2717",        # ✗
    "pending": "." if not UNICODE else "\u25cb",     # ○
    "bar_full": "#" if not UNICODE else "\u2588",    # █
    "bar_empty": "-" if not UNICODE else "\u2591",   # ░
}

SPINNER = ["|", "/", "-", "\\"] if not UNICODE else list("\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f")

LOGO = r"""
    _    _     _____ ____  _____ ____
   / \  | |   |  ___|  _ \| ____|  _ \
  / _ \ | |   | |_  | |_) |  _| | | | |
 / ___ \| |___|  _| |  _ <| |___| |_| |
/_/   \_\_____|_|   |_| \_\_____|____/
"""


def width() -> int:
    return max(60, min(shutil.get_terminal_size((100, 30)).columns, 120))


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def rule(label: str = "") -> None:
    line = "-" * (width() - 2)
    if label:
        line = f"-- {label} " + "-" * max(0, width() - len(label) - 6)
    out(f"{C.grey}{line}{C.reset}")


def banner(status: dict) -> None:
    out(f"{C.cyan}{C.bold}{LOGO}{C.reset}")
    tag = "personal multi-agent system  ยท  policy-gated  ยท  offline-capable"
    out(f"  {C.dim}{tag.replace('ยท', '|')}{C.reset}")
    out()
    for line in status_lines(status):
        out("  " + line)
    out()
    out(f"  {C.dim}type {C.reset}{C.bold}help{C.reset}{C.dim} for commands, "
        f"{C.reset}{C.bold}quit{C.reset}{C.dim} to leave{C.reset}")
    out()


def chip(label: str, ok: bool | None, detail: str = "") -> str:
    if ok is None:
        mark, colour = "?", C.yellow
    elif ok:
        mark, colour = G["ok"], C.green
    else:
        mark, colour = G["fail"], C.red
    text = f"{colour}{mark} {label}{C.reset}"
    return f"{text} {C.dim}{detail}{C.reset}" if detail else text


# ----------------------------------------------------------------- system probe


def _run(argv: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout, shell=False)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def probe_lmstudio(timeout: float = 3.0) -> dict:
    try:
        with urllib.request.urlopen(f"{LMSTUDIO}/v1/models", timeout=timeout) as response:
            data = json.loads(response.read())
        models = [m.get("id") for m in data.get("data", [])]
        return {"up": True, "models": models}
    except (urllib.error.URLError, OSError, ValueError):
        return {"up": False, "models": []}


def probe_harness() -> dict:
    code, stdout, stderr = _run([sys.executable, "scripts/harness.py", "verify"], timeout=25)
    payload = {}
    if code == 0 and stdout.strip():
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            pass
    return {"ok": code == 0, "caps": payload.get("capabilityCount"),
            "gated": payload.get("gated", []), "error": (stderr or "").strip()[:200]}


def probe_memory() -> dict:
    try:
        import memgraph

        con = memgraph.connect()
        memgraph.init(con)
        stats = memgraph.stats(con)
        con.close()
        return {"ok": True, **stats}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160]}


def probe_ultron() -> dict:
    checker = Path("C:/projects/ultron-cli/scripts/gauntlet-check.mjs")
    return {"present": checker.exists(), "node": shutil.which("node") is not None}


def collect_status() -> dict:
    """Probe everything in parallel - serial probes make startup feel broken."""
    result: dict = {}
    jobs = {
        "harness": probe_harness,
        "lmstudio": probe_lmstudio,
        "memory": probe_memory,
        "ultron": probe_ultron,
    }
    threads = []
    for name, fn in jobs.items():
        def worker(name=name, fn=fn):
            try:
                result[name] = fn()
            except Exception as exc:  # noqa: BLE001
                result[name] = {"error": str(exc)[:160]}
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=30)
    return result


def status_lines(status: dict) -> list[str]:
    harness = status.get("harness", {})
    lm = status.get("lmstudio", {})
    mem = status.get("memory", {})
    ultron = status.get("ultron", {})

    lm_detail = ", ".join(m for m in lm.get("models", [])[:2]) or "no model loaded"
    mem_detail = (f"{mem.get('liveFacts', 0)} facts, {mem.get('embedded', 0)} embedded"
                  if mem.get("ok") else mem.get("error", "unavailable"))
    return [
        chip("harness", harness.get("ok"),
             f"{harness.get('caps') or 0} capabilities, {len(harness.get('gated') or [])} gated"),
        chip("lm studio", lm.get("up"), lm_detail),
        chip("memory graph", mem.get("ok"), mem_detail),
        chip("ultron", ultron.get("present") and ultron.get("node"),
             "node + gauntlet parity" if ultron.get("node") else "node missing"),
    ]


# ------------------------------------------------------------------- the motion


@dataclass
class NodeView:
    name: str
    kind: str
    state: str = "pending"       # pending | active | ok | fail | routed | parked
    detail: str = ""
    edge: str = ""
    iteration: int = 0


class GraphMotion:
    """Renders a gauntlet run as a chain that animates in place.

    Why in-place rather than a log: the engine's whole point is that control MOVES
    through a graph and gates decide where. A scrolling log hides that; a redrawn
    chain shows the route being taken, including a forced reroute.
    """

    def __init__(self, nodes: list[dict], stream=sys.stdout) -> None:
        self.views = [NodeView(name=n["name"], kind=n.get("kind", "work")) for n in nodes]
        self.by_name = {v.name: v for v in self.views}
        self.stream = stream
        self.lines = 0
        self.frame = 0
        self.animated = ANSI and stream.isatty()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- painting ---------------------------------------------------------
    def _row(self, view: NodeView) -> str:
        glyph = G.get(view.kind, G["work"])
        if view.state == "active":
            mark = f"{C.blue}{SPINNER[self.frame % len(SPINNER)]}{C.reset}"
            name = f"{C.bold}{view.name}{C.reset}"
        elif view.state == "ok":
            mark, name = f"{C.green}{G['ok']}{C.reset}", view.name
        elif view.state == "fail":
            mark, name = f"{C.red}{G['fail']}{C.reset}", f"{C.red}{view.name}{C.reset}"
        elif view.state == "routed":
            mark, name = f"{C.yellow}{G['arrow']}{C.reset}", view.name
        elif view.state == "parked":
            mark, name = f"{C.yellow}{G['approval']}{C.reset}", f"{C.yellow}{view.name}{C.reset}"
        else:
            mark, name = f"{C.grey}{G['pending']}{C.reset}", f"{C.grey}{view.name}{C.reset}"

        badge = f"{C.grey}[{view.kind}]{C.reset}"
        counter = f"{C.grey}x{view.iteration}{C.reset}" if view.iteration > 1 else ""
        detail = f" {C.dim}{view.detail}{C.reset}" if view.detail else ""
        edge = f"\n      {C.grey}{G['tee']}{C.reset} {view.edge}" if view.edge else ""
        return f"  {C.grey}{glyph}{C.reset} {mark} {name} {badge} {counter}{detail}{edge}"

    def _clear(self) -> None:
        if self.animated and self.lines:
            self.stream.write(f"\x1b[{self.lines}A\x1b[J")

    def paint(self) -> None:
        """Redraw the chain in place.

        When stdout is not a TTY (piped, logged, CI) in-place redraw is impossible,
        so repainting would append the whole chain on every event. In that case the
        caller streams one line per event instead - see `note()`.
        """
        if not self.animated:
            return
        self._clear()
        body = "\n".join(self._row(v) for v in self.views)
        self.stream.write(body + "\n")
        self.stream.flush()
        self.lines = body.count("\n") + 1

    def note(self, text: str) -> None:
        """One compact line, used only when animation is impossible."""
        if not self.animated:
            self.stream.write(f"  {text}\n")
            self.stream.flush()

    def final(self) -> None:
        """Print the finished chain once, for the non-animated path."""
        if not self.animated:
            for view in self.views:
                if view.state != "pending":
                    self.stream.write(self._row(view) + "\n")
            self.stream.flush()

    # -- animation --------------------------------------------------------
    def start(self) -> None:
        self.paint()
        if not self.animated:
            self.note(f"{C.dim}{len(self.views)} nodes; streaming (not a tty){C.reset}")
            return

        def tick() -> None:
            while not self._stop.wait(0.12):
                if any(v.state == "active" for v in self.views):
                    self.frame += 1
                    self.paint()

        self._thread = threading.Thread(target=tick, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        for view in self.views:
            if view.state == "active":
                view.state = "ok"
        self.paint()

    # -- updates ----------------------------------------------------------
    def enter(self, name: str, iteration: int) -> None:
        view = self.by_name.get(name)
        if not view:
            return
        for other in self.views:
            if other.state == "active":
                other.state = "ok"
        view.state = "active"
        view.iteration = iteration
        view.detail = ""
        view.edge = ""
        self.paint()
        self.note(f"{G.get(view.kind, G['work'])} {name} "
                  f"{'x' + str(iteration) if iteration > 1 else ''}...")

    def finish(self, name: str, ok: bool, detail: str = "") -> None:
        view = self.by_name.get(name)
        if not view:
            return
        view.state = "ok" if ok else "fail"
        view.detail = detail
        self.paint()
        self.note(f"  {G['ok'] if ok else G['fail']} {name} {detail}")

    def verdict(self, name: str, verdict: str, action: str, target: str | None,
                forced: bool) -> None:
        view = self.by_name.get(name)
        if not view:
            return
        colour = {"PASS": C.green, "RETRY": C.yellow, "REROUTE": C.magenta,
                  "ESCALATE": C.cyan, "ABORT": C.red}.get(verdict, C.grey)
        flag = f" {C.red}FORCED{C.reset}" if forced else ""
        arrow = f" {G['arrow']} {C.bold}{target}{C.reset}" if target else ""
        view.state = "ok" if verdict == "PASS" else "routed"
        view.edge = f"{colour}{verdict}{C.reset}{arrow}{flag}"
        self.paint()
        self.note(f"  {colour}{verdict}{C.reset} {G['arrow']} {target or action}"
                  f"{' FORCED' if forced else ''}")

    def park(self, name: str, detail: str) -> None:
        view = self.by_name.get(name)
        if view:
            view.state = "parked"
            view.detail = detail
            self.paint()
            self.note(f"  {G['approval']} {name} PARKED: {detail}")


def progress_bar(done: int, total: int, size: int = 26) -> str:
    ratio = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
    filled = int(ratio * size)
    return (f"{C.blue}{G['bar_full'] * filled}{C.grey}{G['bar_empty'] * (size - filled)}"
            f"{C.reset} {int(ratio * 100):3d}% {C.dim}{done}/{total}{C.reset}")


class Spinner:
    """A spinner for a single blocking call. Degrades to one printed line."""

    def __init__(self, label: str, stream=sys.stdout) -> None:
        self.label = label
        self.stream = stream
        self.animated = ANSI and stream.isatty()
        self.started = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Spinner":
        if not self.animated:
            self.stream.write(f"  ... {self.label}\n")
            self.stream.flush()
            return self

        def tick() -> None:
            index = 0
            while not self._stop.wait(0.1):
                mark = SPINNER[index % len(SPINNER)]
                index += 1
                self.stream.write(
                    f"\r  {C.blue}{mark}{C.reset} {self.label} "
                    f"{C.dim}{time.time() - self.started:.1f}s{C.reset}\x1b[K")
                self.stream.flush()

        self._thread = threading.Thread(target=tick, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.animated:
            self.stream.write("\r\x1b[K")
            self.stream.flush()


# ---------------------------------------------------------------------- actions


def cmd_status(_args: str) -> None:
    with Spinner("probing subsystems"):
        status = collect_status()
    rule("status")
    for line in status_lines(status):
        out("  " + line)
    lm = status.get("lmstudio", {})
    if lm.get("models"):
        out(f"  {C.dim}models:{C.reset} " + ", ".join(lm["models"]))
    if not lm.get("up"):
        out(f"  {C.yellow}hint{C.reset} {C.dim}lms server start; lms load alfred-coder-7b -y{C.reset}")
    out()


def cmd_caps(args: str) -> None:
    caller = args.strip() or "owner"
    with Spinner(f"listing capabilities for {caller}"):
        code, stdout, stderr = _run(
            [sys.executable, "scripts/harness.py", "list", "--caller", caller])
    if code != 0:
        out(f"  {C.red}denied or failed{C.reset} {C.dim}{(stderr or '').strip()[:200]}{C.reset}")
        return
    payload = json.loads(stdout)
    rule(f"capabilities: {caller} ({payload.get('trust')})")
    for name, spec in sorted(payload.get("allowed", {}).items()):
        gate = f" {C.yellow}[gated]{C.reset}" if spec.get("gated") else ""
        out(f"  {C.bold}{name:<24}{C.reset} {C.grey}{spec.get('risk','?'):<14}{C.reset}"
            f"{C.dim}{(spec.get('description') or '')[:52]}{C.reset}{gate}")
    out(f"  {C.dim}{payload.get('deniedCount', 0)} denied{C.reset}\n")


def cmd_audit(args: str) -> None:
    limit = int(args.strip()) if args.strip().isdigit() else 12
    path = ROOT / "memory" / "harness-audit.jsonl"
    if not path.exists():
        out(f"  {C.dim}no audit trail yet{C.reset}\n")
        return
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    rule(f"audit trail (last {limit})")
    for raw in lines[-limit:][::-1]:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        decision = record.get("decision", "?")
        colour = {"executed": C.green, "denied": C.red, "dry-run": C.yellow}.get(decision, C.grey)
        out(f"  {C.dim}{record.get('ts','')[:19]}{C.reset} "
            f"{colour}{decision:<9}{C.reset} {C.bold}{record.get('capability','?'):<22}{C.reset}"
            f"{C.grey}{record.get('caller','?')}{C.reset}")
    out()


def cmd_recall(args: str) -> None:
    query = args.strip()
    if not query:
        out(f"  {C.dim}usage: recall <question>{C.reset}\n")
        return
    import memgraph

    con = memgraph.connect()
    memgraph.init(con)
    with Spinner("embedding query"):
        vector = memgraph.embed(query)
    result = memgraph.recall(con, query, k=6, embedding=vector, max_tokens=420)
    con.close()
    rule(f"recall  {C.dim}vector={'on' if vector else 'off'}{C.reset}")
    if result.facts:
        out(result.context)
    elif result.candidates:
        out(f"  {C.yellow}{result.candidates} match(es) found but none fit the budget{C.reset}")
    else:
        out(f"  {C.dim}nothing relevant{C.reset}")
    out(f"  {C.grey}{len(result.facts)} of {result.candidates} facts, ~{result.tokens} tokens, "
        f"sources {result.sources}{C.reset}\n")


def cmd_remember(args: str) -> None:
    """remember <kind> <subject> <predicate> = <object> :: statement"""
    text = args.strip()
    if "::" not in text:
        out(f"  {C.dim}usage: remember <kind> <subject> <predicate>=<object> :: <statement>{C.reset}\n")
        return
    head, statement = text.split("::", 1)
    parts = head.strip().split()
    if len(parts) < 3 or "=" not in head:
        out(f"  {C.dim}usage: remember <kind> <subject> <predicate>=<object> :: <statement>{C.reset}\n")
        return
    kind, subject = parts[0], parts[1]
    predicate, _, obj = " ".join(parts[2:]).partition("=")
    with Spinner("asserting fact"):
        code, stdout, stderr = _run([
            sys.executable, "scripts/harness.py", "run", "graph-assert",
            "--caller", "owner",
            "--param", f"subject={subject}", "--param", f"predicate={predicate.strip()}",
            "--param", f"object={obj.strip()}", "--param", f"kind={kind}",
            "--param", f"statement={statement.strip()}",
        ])
    if code != 0:
        out(f"  {C.red}refused{C.reset} {C.dim}{(stderr or stdout).strip()[:200]}{C.reset}\n")
        return
    try:
        payload = json.loads(stdout)
        note = (f"{C.yellow}superseded {payload['invalidated']}{C.reset}"
                if payload.get("invalidated") else f"{C.green}new{C.reset}")
        out(f"  {G['ok']} fact {payload.get('factId')} {note}\n")
    except json.JSONDecodeError:
        out(f"  {G['ok']} recorded\n")


def cmd_ask(args: str) -> None:
    """Send a prompt to the local model, with memory context injected."""
    prompt = args.strip()
    if not prompt:
        out(f"  {C.dim}usage: ask <prompt>{C.reset}\n")
        return
    lm = probe_lmstudio()
    if not lm["up"]:
        out(f"  {C.red}lm studio is not reachable{C.reset} "
            f"{C.dim}start it: lms server start && lms load alfred-coder-7b -y{C.reset}\n")
        return
    model = next((m for m in lm["models"] if "embed" not in m), lm["models"][0])

    context = ""
    try:
        import memgraph

        con = memgraph.connect()
        memgraph.init(con)
        recalled = memgraph.recall(con, prompt, k=4, embedding=memgraph.embed(prompt),
                                   max_tokens=260)
        con.close()
        context = recalled.context
    except Exception:  # noqa: BLE001 - memory is an enhancement, not a requirement
        context = ""

    system = ("You are Alfred, the Owner's personal assistant. Address him as sir. "
              "Be concise and precise. Never fabricate.")
    if context:
        system += f"\n\nRelevant memory (may be incomplete):\n{context}"

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 600,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{LMSTUDIO}/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")

    with Spinner(f"{model} thinking"):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            body = {"error": str(exc)}

    if "error" in body:
        out(f"  {C.red}failed{C.reset} {C.dim}{body['error']}{C.reset}\n")
        return
    reply = body["choices"][0]["message"]["content"].strip()
    usage = body.get("usage", {})
    rule(f"{model}{'  (memory injected)' if context else ''}")
    for line in reply.splitlines():
        out("  " + line)
    out(f"  {C.grey}in {usage.get('prompt_tokens','?')} / out "
        f"{usage.get('completion_tokens','?')} tokens{C.reset}\n")


def cmd_graph(args: str) -> None:
    spec_path = _resolve_spec(args.strip())
    if not spec_path:
        return
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    rule(f"graph: {spec.get('name', spec_path.stem)}")
    for node in spec.get("nodes", []):
        kind = node.get("kind", "work")
        glyph = G.get(kind, G["work"])
        deps = ", ".join(node.get("depends_on") or []) or "-"
        out(f"  {C.grey}{glyph}{C.reset} {C.bold}{node['name']:<18}{C.reset}"
            f"{C.grey}[{kind}]{C.reset} {C.dim}{node.get('agent','?'):<20} deps={deps}{C.reset}")
        for verdict, target in (node.get("on") or {}).items():
            colour = {"PASS": C.green, "RETRY": C.yellow, "REROUTE": C.magenta,
                      "ESCALATE": C.cyan, "ABORT": C.red}.get(verdict, C.grey)
            out(f"        {C.grey}{G['tee']}{C.reset} {colour}{verdict:<9}{C.reset}"
                f"{G['arrow']} {target}")
        if node.get("compensate"):
            out(f"        {C.grey}{G['end']}{C.reset} {C.dim}undo: {node['compensate']}{C.reset}")
    out()


def _resolve_spec(name: str) -> Path | None:
    if not name:
        specs = sorted((ROOT / "workflows").glob("*.json"))
        out(f"  {C.dim}available specs:{C.reset}")
        for path in specs:
            out(f"    {path.stem}")
        out()
        return None
    candidate = Path(name)
    for option in (candidate, ROOT / "workflows" / name,
                   ROOT / "workflows" / f"{name}.json"):
        if option.exists():
            return option
    out(f"  {C.red}no such spec:{C.reset} {name}\n")
    return None


def _build_executor(motion: GraphMotion, prefer: str | None = None):
    """A provider-backed executor with tier routing, or a stub if nothing is up.

    Returns (executor, report_or_None, label) so the caller can show where the work
    actually went - routing you cannot see is routing you cannot trust.
    """
    import executors

    try:
        available = {prefer} if prefer else executors.reachable_providers(timeout=5.0)
    except Exception:  # noqa: BLE001
        available = set()

    if not available:
        return executors.make_stub_executor(delay=0.25), None, "stub executor (nothing reachable)"

    report = executors.ExecutorReport()
    executor = executors.make_executor(prefer=prefer, report=report, probe_timeout=5.0)
    label = f"providers: {', '.join(sorted(available))}"
    return executor, report, label


def cmd_run(args: str) -> None:
    """Run a gauntlet spec with live graph motion.

    Usage: run <spec> [task ...] [--local | --provider <name>]
    """
    tokens = args.split()
    prefer = None
    force = False
    if "--anyway" in tokens:
        tokens.remove("--anyway")
        force = True
    if "--local" in tokens:
        tokens.remove("--local")
        prefer = "lmstudio"
    if "--provider" in tokens:
        index = tokens.index("--provider")
        if index + 1 < len(tokens):
            prefer = tokens[index + 1]
            del tokens[index:index + 2]

    spec_name = tokens[0] if tokens else ""
    task = " ".join(tokens[1:]) or "demonstrate the graph"
    spec_path = _resolve_spec(spec_name)
    if not spec_path:
        return

    import gauntlet

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    errors = gauntlet.validate_spec(spec)
    if errors:
        out(f"  {C.red}spec is invalid{C.reset}")
        for error in errors:
            out(f"    - {error}")
        out()
        return

    motion = GraphMotion(spec["nodes"])
    with Spinner("resolving providers"):
        executor, report, label = _build_executor(motion, prefer)

    # Warn BEFORE spending minutes. A 7B on CPU is fine on an idle machine and
    # unusable under memory pressure; measured turning an 8s call into a timeout.
    if report is not None:
        import executors

        provider = next(iter(executor.available), "lmstudio") if executor.available else "lmstudio"
        if provider in ("lmstudio", "ollama"):
            with Spinner("checking local model latency"):
                check = executors.preflight_latency(provider)
            warning = executors.advise(check, len(spec["nodes"]))
            if warning:
                out(f"  {C.yellow}slow{C.reset} {C.dim}{warning}{C.reset}")
            # Reachable is not the same as usable: the /v1/models endpoint answers
            # instantly while chat times out under memory pressure. Refusing beats
            # silently burning minutes, and a stub would be fake work.
            if not check["ok"] and not force:
                out(f"  {C.red}refusing to start{C.reset} {C.dim}the model cannot answer a "
                    f"5-token call, so a {len(spec['nodes'])}-node graph will not finish."
                    f"{C.reset}")
                out(f"  {C.dim}options: free memory | load a smaller model | add an API key | "
                    f"{C.reset}{C.bold}run {spec_path.stem} --anyway{C.reset}\n")
                return

    rule(f"run: {spec.get('name', spec_path.stem)}  {C.dim}{label}{C.reset}")
    motion.start()

    def observer(event: str, payload: dict) -> None:
        if event == "enter":
            motion.enter(payload["node"], payload.get("iteration", 1))
        elif event == "finish":
            motion.finish(payload["node"], payload.get("ok", True),
                          payload.get("detail", ""))
        elif event == "verdict":
            motion.verdict(payload["node"], payload["verdict"], payload["action"],
                           payload.get("target"), payload.get("forced", False))
        elif event == "park":
            motion.park(payload["node"], payload.get("detail", "awaiting approval"))

    # Always checkpoint: a run that parks must be genuinely resumable, otherwise
    # the "resume with ..." hint printed below would be a lie.
    checkpointer = gauntlet.Checkpointer()
    try:
        result = gauntlet.run_gauntlet(spec, task, executor, max_node_runs=24,
                                       observer=observer, checkpointer=checkpointer)
    finally:
        motion.stop()
        checkpointer.close()

    _report_result(result, report, spec_path)


def _report_result(result, report, spec_path) -> None:
    colour = {"passed": C.green, "aborted": C.red,
              "interrupted": C.yellow, "budget_exhausted": C.yellow}.get(result.status, C.grey)
    out(f"\n  {colour}{C.bold}{result.status.upper()}{C.reset} {C.dim}{result.reason}{C.reset}")
    forced = [r for r in result.runs if (r.routing or {}).get("forced")]
    out(f"  {C.grey}{len(result.runs)} node runs, {len(forced)} forced route(s){C.reset}")
    if report is not None:
        out(f"  {C.grey}{report.summary()}{C.reset}")
        slowest = sorted(report.routes, key=lambda r: -r.ms)[:3]
        if slowest:
            detail = ", ".join(f"{r.agent.split('-')[-1]} {r.ms}ms [{r.tier}]" for r in slowest)
            out(f"  {C.grey}slowest: {detail}{C.reset}")
    if result.status == "interrupted":
        out(f"  {C.yellow}parked{C.reset} {C.dim}resume: "
            f"{C.reset}{C.bold}resume {result.run_id} <node>{C.reset}")
    out()


def _stub_executor(motion: GraphMotion):
    """Deterministic executor used when no model is loaded."""
    def executor(agent, task, timeout=None):
        time.sleep(0.35)  # visible motion; this path is for demonstration
        if "review" in agent or "gate" in agent:
            return '{"verdict":"PASS","reasons":[],"confidence":1.0}'
        return f"[{agent}] completed"
    return executor


def _local_executor(model: str, motion: GraphMotion):
    """Drive real nodes through the local model. Gates get a strict JSON prompt."""
    def executor(agent, task, timeout=None):
        is_gate = "VERDICT" in task or "GATE" in task
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content":
                    "You are a node in an execution graph. Be terse."
                    + (" Reply with ONE JSON object only." if is_gate else "")},
                {"role": "user", "content": task[:4000]},
            ],
            "temperature": 0.0 if is_gate else 0.4,
            "max_tokens": 220 if is_gate else 400,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{LMSTUDIO}/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout or 180) as response:
                body = json.loads(response.read())
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            return f"[ERROR] local model: {exc}"
    return executor


def cmd_mem(_args: str) -> None:
    import memgraph

    con = memgraph.connect()
    memgraph.init(con)
    stats = memgraph.stats(con)
    changed = [f for f in con.execute(
        "SELECT f.statement, e.name AS subject, f.predicate, f.superseded_by "
        "FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
        "WHERE f.t_invalid IS NOT NULL ORDER BY f.t_invalid DESC LIMIT 5")]
    con.close()
    rule("memory graph")
    out(f"  {C.bold}{stats['liveFacts']}{C.reset} current  "
        f"{C.grey}{stats['supersededFacts']} superseded  "
        f"{stats['entities']} entities  {stats['episodes']} episodes  "
        f"{stats['embedded']} embedded{C.reset}")
    for kind, count in sorted(stats["byKind"].items(), key=lambda kv: -kv[1]):
        out(f"    {kind:<12} {progress_bar(count, stats['facts'], 18)}")
    if changed:
        out(f"\n  {C.dim}what changed:{C.reset}")
        for row in changed:
            out(f"    {C.grey}{row['subject']} {row['predicate']}{C.reset} "
                f"{C.dim}{(row['statement'] or '')[:70]}{C.reset}")
    out()


def cmd_dash(_args: str) -> None:
    out(f"  {C.dim}starting the dashboard (read-only, loopback + token)...{C.reset}")
    subprocess.Popen([sys.executable, "-u", "scripts/dashboard.py"], cwd=str(ROOT))
    out(f"  {G['ok']} launched; the URL with its token is printed in that window\n")


def _downloaded_models() -> tuple[list[str], list[str]]:
    """(chat models, embedding models) that LM Studio has on disk.

    Discovered rather than hardcoded, so a newly downloaded model is picked up
    without editing this file.
    """
    code, stdout, _ = _run(["lms", "ls"], timeout=30)
    if code != 0:
        return [], []
    chat: list[str] = []
    embed: list[str] = []
    section = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("LLM"):
            section = "chat"
            continue
        if upper.startswith("EMBEDDING"):
            section = "embed"
            continue
        if upper.startswith("YOU HAVE") or upper.startswith("PARAMS"):
            continue
        identifier = stripped.split()[0]
        if "/" in identifier or identifier.replace("-", "").replace(".", "").isalnum():
            (chat if section == "chat" else embed if section == "embed" else []).append(identifier)
    return chat, embed


def _pick_chat_model(candidates: list[str]) -> str | None:
    """Prefer the Owner's own fine-tune, then anything that is not an embedder."""
    if not candidates:
        return None
    for name in candidates:
        if "alfred" in name.lower():
            return name
    return candidates[0]


def cmd_lms(args: str) -> None:
    """Start LM Studio's server and load models with memory-lean settings.

    Defaults matter here. LM Studio's own defaults were 8192 context x 4 parallel
    slots, whose KV cache pushed this machine to 2.4GB free and turned an 8s call
    into a 120s timeout. Reloading at 4096 x 1 freed ~5.7GB and brought the same
    call back to 4.8s. One slot is right anyway: the graph engine runs nodes
    sequentially.
    """
    action = args.strip() or "up"
    if action in ("up", "lean"):
        with Spinner("starting lm studio server"):
            _run(["lms", "server", "start"], timeout=60)
        lm = probe_lmstudio()
        if not lm["up"]:
            out(f"  {C.red}server did not come up{C.reset}\n")
            return

        chat_models, embed_models = _downloaded_models()
        loaded = [m for m in lm["models"] if "embed" not in m]
        if not loaded:
            target = _pick_chat_model(chat_models)
            if not target:
                out(f"  {C.yellow}no chat model is downloaded{C.reset} "
                    f"{C.dim}download one in LM Studio, then run {C.reset}"
                    f"{C.bold}lms up{C.reset}\n")
                return
            with Spinner(f"loading {target} (4096 ctx, 1 slot)"):
                _run(["lms", "load", target, "-y",
                      "--context-length", "4096", "--parallel", "1", "--ttl", "3600"],
                     timeout=600)
        if not any("embed" in m for m in probe_lmstudio()["models"]) and embed_models:
            with Spinner(f"loading {embed_models[0]}"):
                _run(["lms", "load", embed_models[0], "-y", "--ttl", "3600"], timeout=180)

        models = probe_lmstudio()["models"]
        out(f"  {G['ok']} lm studio ready: {', '.join(models) or 'none'}")
        with Spinner("checking latency"):
            import executors

            check = executors.preflight_latency()
        tone = C.green if check["ok"] and not check["slow"] else C.yellow
        out(f"  {tone}{check['seconds']}s{C.reset} {C.dim}for a 5-token call"
            f"{'' if check['ok'] else ' (timed out)'}{C.reset}\n")
    elif action == "lean-reload":
        # Explicit: unload everything and reload lean. Fixes accumulated KV cache.
        with Spinner("unloading all models"):
            _run(["lms", "unload", "--all"], timeout=60)
        cmd_lms("up")
    elif action == "models":
        chat_models, embed_models = _downloaded_models()
        rule("downloaded models")
        for name in chat_models:
            marker = f"{C.green}*{C.reset}" if name == _pick_chat_model(chat_models) else " "
            out(f"  {marker} {C.bold}{name}{C.reset} {C.grey}chat{C.reset}")
        for name in embed_models:
            out(f"    {C.bold}{name}{C.reset} {C.grey}embedding{C.reset}")
        if not chat_models and not embed_models:
            out(f"  {C.dim}none found (is the lms CLI on PATH?){C.reset}")
        out(f"  {C.dim}* would be loaded by {C.reset}{C.bold}lms up{C.reset}\n")
    elif action in ("ps", "status"):
        code, stdout, _ = _run(["lms", "ps"], timeout=30)
        out(stdout.strip() + "\n")
    else:
        out(f"  {C.dim}usage: lms up | lms lean-reload | lms models | lms ps{C.reset}\n")


def cmd_embed(_args: str) -> None:
    import memgraph

    con = memgraph.connect()
    memgraph.init(con)
    pending = int(con.execute(
        "SELECT COUNT(*) FROM mg_fact WHERE embedding IS NULL").fetchone()[0])
    if not pending:
        out(f"  {G['ok']} every fact already has a vector\n")
        con.close()
        return

    state = {"done": 0}

    def progress(done: int, total: int) -> None:
        state["done"] = done
        sys.stdout.write("\r  " + progress_bar(done, total) + "\x1b[K")
        sys.stdout.flush()

    report = memgraph.backfill_embeddings(con, progress=progress)
    con.close()
    out("\r  " + progress_bar(report["embedded"], max(pending, 1)) + "\x1b[K")
    out(f"  {G['ok']} embedded {report['embedded']}, remaining {report['remaining']}\n")


def cmd_test(_args: str) -> None:
    suites = ["test_harness.py", "test_gauntlet.py", "test_memgraph.py",
              "test_dashboard.py", "test_workflow.py", "test_ultron_parity.py"]
    rule("test suites")
    total = failed = 0
    for suite in suites:
        with Spinner(f"running {suite}"):
            code, _stdout, stderr = _run([sys.executable, f"scripts/{suite}"], timeout=300)
        count = 0
        for line in (stderr or "").splitlines():
            if line.startswith("Ran ") and " test" in line:
                try:
                    count = int(line.split()[1])
                except (IndexError, ValueError):
                    count = 0
        total += count
        mark = f"{C.green}{G['ok']}{C.reset}" if code == 0 else f"{C.red}{G['fail']}{C.reset}"
        if code != 0:
            failed += 1
        out(f"  {mark} {suite:<24} {C.grey}{count} tests{C.reset}")
    tone = C.green if not failed else C.red
    out(f"  {tone}{total} tests, {failed} failing suite(s){C.reset}\n")


def cmd_models(_args: str) -> None:
    """Show every provider, whether it is usable, and how to enable it."""
    import providers as P

    with Spinner("probing providers"):
        statuses = P.probe_all(timeout=5.0)
    rule("model providers")
    for status in statuses:
        spec = P.PROVIDERS[status.name]
        if status.reachable:
            state, colour = "reachable", C.green
        elif status.configured:
            state, colour = "unreachable", C.red
        else:
            state, colour = "no key", C.yellow
        cost = "free" if spec.free else spec.tier
        out(f"  {colour}{state:<12}{C.reset} {C.bold}{status.name:<12}{C.reset}"
            f"{C.grey}{cost:<9}{C.reset}{C.dim}key={status.key}{C.reset}"
            f"{'  ' + C.dim + status.detail[:44] + C.reset if status.detail else ''}")
        if status.models:
            out(f"               {C.dim}{', '.join(status.models[:3])}{C.reset}")
    missing = [P.PROVIDERS[s.name].env_key for s in statuses if not s.configured]
    if missing:
        out(f"\n  {C.dim}to enable:{C.reset} "
            f"{C.bold}python scripts/providers.py set-key {missing[0]}{C.reset}"
            f" {C.dim}(input hidden, stored in secrets/){C.reset}")
    out()


def cmd_runs(_args: str) -> None:
    """Checkpointed runs, newest first, with the parked ones called out."""
    import gauntlet

    cp = gauntlet.Checkpointer()
    try:
        rows = cp.runs(15)
    finally:
        cp.close()
    if not rows:
        out(f"  {C.dim}no checkpointed runs yet{C.reset}\n")
        return
    rule("runs")
    for row in rows:
        colour = {"passed": C.green, "aborted": C.red, "interrupted": C.yellow,
                  "running": C.blue}.get(row["status"], C.grey)
        out(f"  {colour}{row['status']:<12}{C.reset}{C.bold}{row['run_id']:<38}{C.reset}"
            f"{C.grey}steps={row['supersteps'] or 0}{C.reset}")
        if row["status"] in ("interrupted", "running") and row["reason"]:
            out(f"    {C.dim}{row['reason'][:100]}{C.reset}")
    out(f"  {C.dim}resume: {C.reset}{C.bold}resume <run-id> [node]{C.reset}\n")


def cmd_resume(args: str) -> None:
    """resume <run-id> [approval-node] - continue a parked or crashed run."""
    parts = args.split()
    if not parts:
        cmd_runs("")
        return
    run_id, approvals = parts[0], parts[1:]

    import gauntlet

    cp = gauntlet.Checkpointer()
    try:
        record = cp.run(run_id)
        if not record:
            out(f"  {C.red}no such run:{C.reset} {run_id}\n")
            return
        spec = json.loads(record["spec"])
        motion = GraphMotion(spec["nodes"])

        def observer(event: str, payload: dict) -> None:
            if event == "enter":
                motion.enter(payload["node"], payload.get("iteration", 1))
            elif event == "finish":
                motion.finish(payload["node"], payload.get("ok", True),
                              payload.get("detail", ""))
            elif event == "verdict":
                motion.verdict(payload["node"], payload["verdict"], payload["action"],
                               payload.get("target"), payload.get("forced", False))
            elif event == "park":
                motion.park(payload["node"], payload.get("detail", "awaiting approval"))

        lm = probe_lmstudio()
        executor, report, label = _build_executor(motion)

        rule(f"resume: {run_id}  {C.dim}{label}{C.reset}"
             + (f"  {C.dim}approving {', '.join(approvals)}{C.reset}" if approvals else ""))
        motion.start()
        try:
            result = gauntlet.run_gauntlet(
                spec, record["task"], executor, observer=observer,
                checkpointer=cp, run_id=run_id, resume=True, approved=approvals,
                max_node_runs=24)
        finally:
            motion.stop()
    finally:
        cp.close()

    _report_result(result, report, None)


class PolicyChain:
    """Renders the harness policy chain as motion.

    Every row is a real control inside `harness.run_capability` that can refuse the
    call - caller resolution, token auth, deny-by-default, the per-caller allowlist,
    the gate, parameter validation, argv construction, execution, audit. Showing them
    as a chain makes visible what is otherwise a single silent yes/no.
    """

    STAGES = [
        ("caller", "identify the caller and its trust level"),
        ("auth", "token, if this caller needs one"),
        ("defined", "capability exists in the signed policy"),
        ("allowlist", "this caller is permitted to run it"),
        ("gate", "gated capabilities need explicit approval"),
        ("params", "types, lengths, enums, path confinement"),
        ("argv", "argv built without a shell, injection scanned"),
        ("execute", "run it"),
        ("audit", "append to the trail"),
    ]

    def __init__(self, capability: str, caller: str, stream=sys.stdout) -> None:
        self.capability = capability
        self.caller = caller
        self.stream = stream
        self.animated = ANSI and stream.isatty()
        self.state: dict[str, tuple[bool | None, str]] = {
            name: (None, "") for name, _ in self.STAGES}
        self.lines = 0
        self.frame = 0
        self.active: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _row(self, name: str, description: str) -> str:
        ok, detail = self.state[name]
        if ok is None:
            if name == self.active:
                mark = f"{C.blue}{SPINNER[self.frame % len(SPINNER)]}{C.reset}"
            else:
                mark = f"{C.grey}{G['pending']}{C.reset}"
            label = f"{C.grey}{name}{C.reset}"
        elif ok:
            mark, label = f"{C.green}{G['ok']}{C.reset}", name
        else:
            mark, label = f"{C.red}{G['fail']}{C.reset}", f"{C.red}{name}{C.reset}"
        note = detail or (description if ok is None else "")
        return f"  {C.grey}{G['gate']}{C.reset} {mark} {label:<11}{C.dim}{note}{C.reset}"

    def paint(self) -> None:
        if not self.animated:
            return
        if self.lines:
            self.stream.write(f"\x1b[{self.lines}A\x1b[J")
        body = "\n".join(self._row(n, d) for n, d in self.STAGES)
        self.stream.write(body + "\n")
        self.stream.flush()
        self.lines = body.count("\n") + 1

    def start(self) -> None:
        self.paint()
        if not self.animated:
            return

        def tick() -> None:
            while not self._stop.wait(0.12):
                if self.active:
                    self.frame += 1
                    self.paint()

        self._thread = threading.Thread(target=tick, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self.active = None
        self.paint()

    def observe(self, stage: str, ok: bool, detail: str) -> None:
        if stage not in self.state:
            return
        self.state[stage] = (ok, detail)
        # "execute" fires twice: once entering, once with the exit code.
        self.active = stage if (stage == "execute" and ok and detail == "running") else None
        self.paint()
        if not self.animated:
            mark = G["ok"] if ok else G["fail"]
            colour = C.green if ok else C.red
            self.stream.write(f"  {colour}{mark}{C.reset} {stage:<11}{C.dim}{detail}{C.reset}\n")
            self.stream.flush()


def cmd_do(args: str) -> None:
    """do <capability> [k=v ...] [--caller X] [--approve] [--dry-run]

    Runs a harness capability and renders the policy chain as it is checked.
    """
    tokens = args.split()
    if not tokens:
        out(f"  {C.dim}usage: do <capability> [k=v ...] [--caller X] [--approve] [--dry-run]"
            f"{C.reset}")
        out(f"  {C.dim}see {C.reset}{C.bold}caps{C.reset}{C.dim} for what is available{C.reset}\n")
        return

    caller, approve, dry_run = "owner", False, False
    params: dict[str, str] = {}
    capability = tokens[0]
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--caller" and index + 1 < len(tokens):
            caller = tokens[index + 1]
            index += 2
            continue
        if token == "--approve":
            approve = True
        elif token in ("--dry-run", "--dry"):
            dry_run = True
        elif "=" in token:
            key, _, value = token.partition("=")
            params[key] = value
        index += 1

    sys.path.insert(0, str(ROOT / "scripts"))
    import harness

    rule(f"harness: {capability}  {C.dim}caller={caller}"
         f"{' approved' if approve else ''}{' dry-run' if dry_run else ''}{C.reset}")
    chain = PolicyChain(capability, caller)
    chain.start()
    try:
        policy = harness.verify_policy()
        result = harness.run_capability(
            policy, capability, caller, os.environ.get("ALFRED_HARNESS_TOKEN"),
            params, approve, dry_run, observer=chain.observe)
    except harness.PolicyError as exc:
        chain.stop()
        out(f"\n  {C.red}POLICY ERROR{C.reset} {C.dim}{exc}{C.reset}\n")
        return
    except harness.Denied as exc:
        chain.stop()
        out(f"\n  {C.red}DENIED{C.reset} {C.dim}{exc}{C.reset}\n")
        return
    except harness.BadInput as exc:
        chain.stop()
        out(f"\n  {C.yellow}BAD INPUT{C.reset} {C.dim}{exc}{C.reset}\n")
        return
    finally:
        chain.stop()

    colour = C.green if result.ok else C.red
    out(f"\n  {colour}{'OK' if result.ok else 'FAILED'}{C.reset} "
        f"{C.dim}exit {result.exit_code}{C.reset}")
    body = (result.stdout or "").rstrip()
    if body:
        for line in body.splitlines()[:24]:
            out(f"  {C.dim}|{C.reset} {line}")
        extra = len(body.splitlines()) - 24
        if extra > 0:
            out(f"  {C.grey}... {extra} more line(s){C.reset}")
    if result.stderr and not result.ok:
        out(f"  {C.red}{result.stderr.strip()[:300]}{C.reset}")
    out()


def cmd_help(_args: str) -> None:
    rows = [
        ("status", "probe harness, lm studio, memory, ultron"),
        ("caps [caller]", "capability surface for a caller (owner/kiro-agent/local-model)"),
        ("do <capability>", "run a harness capability, showing the policy chain"),
        ("audit [n]", "recent harness decisions"),
        ("graph [spec]", "draw a gauntlet graph (no args lists specs)"),
        ("run <spec> [task]", "execute a graph with live motion"),
        ("runs", "checkpointed runs; parked ones are called out"),
        ("resume <id> [node]", "continue a parked or crashed run, approving a node"),
        ("mem", "memory graph summary and what changed"),
        ("recall <question>", "hybrid recall from the memory graph"),
        ("remember ...", "assert a fact: remember <kind> <subj> <pred>=<obj> :: <statement>"),
        ("ask <prompt>", "ask the local model, with memory injected"),
        ("models", "provider status: local, nvidia, deepseek, openrouter"),
        ("embed", "embed facts that have no vector yet"),
        ("lms up|models", "start lm studio / list local models"),
        ("test", "run the test suites"),
        ("dash", "launch the read-only dashboard"),
        ("clear", "clear the screen"),
        ("quit", "leave"),
    ]
    rule("commands")
    for name, description in rows:
        out(f"  {C.bold}{name:<20}{C.reset}{C.dim}{description}{C.reset}")
    out()


COMMANDS = {
    "status": cmd_status, "st": cmd_status,
    "caps": cmd_caps, "capabilities": cmd_caps,
    "do": cmd_do, "harness": cmd_do,
    "audit": cmd_audit,
    "graph": cmd_graph, "g": cmd_graph,
    "run": cmd_run,
    "runs": cmd_runs,
    "resume": cmd_resume,
    "mem": cmd_mem, "memory": cmd_mem,
    "recall": cmd_recall, "r": cmd_recall,
    "remember": cmd_remember,
    "ask": cmd_ask, "a": cmd_ask,
    "embed": cmd_embed,
    "models": cmd_models, "providers": cmd_models,
    "lms": cmd_lms,
    "test": cmd_test,
    "dash": cmd_dash, "dashboard": cmd_dash,
    "help": cmd_help, "?": cmd_help,
}


#: The canonical name for each command, in the order `help` lists them. Aliases live
#: in COMMANDS for convenience but are excluded from completion and "did you mean"
#: suggestions, because being offered `st` instead of `status` is worse than useless.
CANONICAL = [
    "status", "caps", "do", "audit", "graph", "run", "runs", "resume", "mem",
    "recall", "remember", "ask", "models", "embed", "lms", "test", "dash",
    "help", "clear", "quit",
]


def _completion_options() -> dict[str, list[str]]:
    """Argument suggestions per command. Cheap to compute, so done per session."""
    specs = sorted(p.stem for p in (ROOT / "workflows").glob("*.json"))
    callers = ["owner", "kiro-agent", "local-model", "scheduled"]
    capabilities: list[str] = []
    try:
        policy = json.loads((ROOT / "policy" / "harness-policy.json")
                            .read_text(encoding="utf-8"))
        capabilities = sorted(policy.get("capabilities", {}))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "run": specs, "graph": specs, "g": specs,
        "do": capabilities, "harness": capabilities,
        "caps": callers, "capabilities": callers,
        "lms": ["up", "lean-reload", "models", "ps"],
        "remember": ["decision", "learning", "fact", "preference", "outcome"],
    }


def _did_you_mean(typed: str) -> str:
    """Suggest the nearest canonical command, scored by shared prefix length.

    Matching on a fixed 2 characters produced nonsense like "recal -> resume", so
    the whole shared prefix is scored and a single character is not enough to guess.
    """
    def shared(candidate: str) -> int:
        length = 0
        for a, b in zip(typed, candidate):
            if a != b:
                break
            length += 1
        return length

    best = max(CANONICAL, key=shared)
    if shared(best) < 2:
        return f"; try {C.reset}{C.bold}help{C.reset}"
    return f"; did you mean {C.reset}{C.bold}{best}{C.reset}{C.dim}?"


def repl() -> int:
    with Spinner("waking up"):
        status = collect_status()
    os.system("cls" if os.name == "nt" else "clear")
    banner(status)

    import lineedit

    editor = lineedit.LineEditor(
        history=lineedit.History(ROOT / "memory" / ".console_history"),
        completer=lineedit.make_completer(CANONICAL, _completion_options()),
    )
    if editor.interactive:
        out(f"  {C.dim}tab completes, up/down recalls history{C.reset}\n")

    while True:
        try:
            raw = editor.read(f"{C.cyan}{C.bold}alfred{C.reset}{C.grey} >{C.reset} ").strip()
        except KeyboardInterrupt:
            # Ctrl+C cancels the line, it does not end the session - that is what
            # every other shell does, and losing a session to a stray Ctrl+C is rude.
            out(f"  {C.dim}(cancelled){C.reset}")
            continue
        except EOFError:
            out("\n  goodbye, sir\n")
            return 0
        if not raw:
            continue
        name, _, args = raw.partition(" ")
        key = name.lower()
        if key in ("quit", "exit", "q"):
            out("  goodbye, sir\n")
            return 0
        if key == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            continue
        handler = COMMANDS.get(key)
        if not handler:
            out(f"  {C.dim}unknown command {C.reset}{C.bold}{name}{C.reset}"
                f"{C.dim}{_did_you_mean(key)}{C.reset}\n")
            continue
        try:
            handler(args)
        except KeyboardInterrupt:
            out(f"\n  {C.yellow}interrupted{C.reset}\n")
        except Exception as exc:  # noqa: BLE001 - the console must survive anything
            out(f"  {C.red}{type(exc).__name__}{C.reset} {C.dim}{str(exc)[:300]}{C.reset}\n")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        out("usage: console.py [command ...]   (no args = interactive)")
        cmd_help("")
        return 0
    if args:
        name, rest = args[0].lower(), " ".join(args[1:])
        handler = COMMANDS.get(name)
        if handler:
            handler(rest)
            return 0
        # Not a console verb. `alfred "write a function"` has always meant "run this
        # coding task on the local model", and this entrypoint must not break that
        # contract just because it grew a console.
        task = " ".join(args)
        out(f"  {C.dim}not a console command; sending to the local coder{C.reset}")
        script = ROOT / "scripts" / "alfred.ps1"
        if not script.exists():
            out(f"  {C.red}missing {script}{C.reset}")
            return 2
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script), task],
            cwd=str(ROOT), shell=False,
        )
        return completed.returncode
    return repl()


if __name__ == "__main__":
    sys.exit(main())
