#!/usr/bin/env python3
"""Alfred Dashboard - a local, zero-dependency control surface.

WHY THIS EXISTS
---------------
Alfred is driven from several places: the Kiro CLI, the Ultron CLI, local models
via LM Studio, and Notion AI over MCP. Each of those is a *text* surface. None of
them answers "what is this system doing right now?" at a glance. This does.

DESIGN CONSTRAINTS (deliberate, do not "improve" these away)
-----------------------------------------------------------
* **Zero dependencies.** Python standard library only. No pip install, no npm, no
  CDN. It must work offline on a fresh clone. Alfred's whole premise is that the
  Owner owns and understands the stack.
* **Loopback only.** Binds 127.0.0.1. Never 0.0.0.0. This process can read the
  audit trail and memory, so it must not be reachable from the network.
* **Token gated.** A random token is minted per start and required on every
  request. Without it, any other local process (including a local model with
  shell access) could read the dashboard.
* **Read-only.** v1 executes nothing. It observes. Triggering capabilities from a
  browser is a genuine privilege-escalation surface (an XSS or a stray fetch
  becomes a code-execution path), so mutations stay behind the signed policy and
  the CLI until the auth story is properly designed.
* **Never serves secrets.** secrets/ and the signing key are not exposed, and the
  policy is served without any token/credential fields.

USAGE
-----
    python scripts/dashboard.py            # pick a free port, print the URL
    python scripts/dashboard.py --port 7373
    python scripts/dashboard.py --no-browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent

POLICY_PATH = ROOT / "policy" / "harness-policy.json"
AUDIT_PATH = ROOT / "memory" / "harness-audit.jsonl"
TODO_PATH = ROOT / "memory" / "todo.md"
MEGAMIND_DB = ROOT / "memory" / "megamind.db"
MEMORY_JSONL = ROOT / "memory" / "memory.jsonl"
RUNS_DIR = ROOT / "memory" / "workflows"
RUNS_DB = ROOT / "memory" / "gauntlet-runs.db"

# Paths the dashboard must never read from, even by accident.
FORBIDDEN_DIRS = {(ROOT / "secrets").resolve()}

TOKEN = secrets.token_urlsafe(24)


# --------------------------------------------------------------------- helpers


def _safe_read_text(path: Path, limit: int = 200_000) -> str:
    """Read a text file, refusing anything under a forbidden directory."""
    resolved = path.resolve()
    for bad in FORBIDDEN_DIRS:
        if bad == resolved or bad in resolved.parents:
            raise PermissionError(f"refusing to read {path}")
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8", errors="replace")[:limit]


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Last `limit` valid JSON objects from a .jsonl file, newest first."""
    text = _safe_read_text(path, limit=4_000_000)
    if not text:
        return []
    out: list[dict] = []
    for line in text.splitlines()[-(limit * 3):]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    out.reverse()
    return out[:limit]


def _run(argv: list[str], timeout: int = 25) -> tuple[int, str, str]:
    """Run a command with no shell. Returns (exit, stdout, stderr)."""
    try:
        proc = subprocess.run(
            argv, cwd=str(ROOT), capture_output=True, text=True,
            timeout=timeout, shell=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


# ------------------------------------------------------------------- collectors


def collect_health() -> dict:
    """Harness integrity - the single most important signal on the page."""
    code, out, err = _run([sys.executable, "scripts/harness.py", "verify"])
    payload: dict = {}
    if code == 0 and out.strip():
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            pass
    return {
        "harnessOk": code == 0,
        "exitCode": code,
        "signatureValid": bool(payload.get("signatureValid")),
        "denyByDefault": payload.get("denyByDefault"),
        "capabilityCount": payload.get("capabilityCount"),
        "gated": payload.get("gated", []),
        "callers": payload.get("callers", {}),
        # A failure here means the harness is refusing to run ANYTHING.
        "error": (err or out).strip()[:600] if code != 0 else "",
    }


def collect_policy() -> dict:
    """The capability surface per caller. Credentials are never included."""
    try:
        policy = json.loads(_safe_read_text(POLICY_PATH))
    except json.JSONDecodeError as exc:
        return {"error": f"policy is not valid JSON: {exc}"}
    if not policy:
        return {"error": "policy not found"}

    caps = policy.get("capabilities", {})
    callers = {}
    for name, spec in policy.get("callers", {}).items():
        allowed = spec.get("capabilities", [])
        if allowed == ["*"]:
            resolved = sorted(caps)
        else:
            resolved = sorted(c for c in allowed if c in caps)
        callers[name] = {
            "trust": spec.get("trust"),
            "authRequired": bool(spec.get("authRequired", False)),
            "allowed": resolved,
            "deniedCount": len(caps) - len(resolved),
        }

    return {
        "denyByDefault": policy.get("settings", {}).get("denyByDefault"),
        "callers": callers,
        "capabilities": {
            name: {
                "risk": spec.get("risk"),
                "gated": bool(spec.get("gated", False)),
                "description": spec.get("description", ""),
            }
            for name, spec in sorted(caps.items())
        },
    }


def collect_audit(limit: int = 60) -> dict:
    """Recent harness attempts: executed, denied, and dry-run."""
    records = _tail_jsonl(AUDIT_PATH, limit)
    counts = {"executed": 0, "denied": 0, "dry-run": 0, "other": 0}
    for r in records:
        d = r.get("decision", "other")
        counts[d if d in counts else "other"] += 1
    entries = [
        {
            "ts": r.get("ts", ""),
            "caller": r.get("caller", ""),
            "trust": r.get("trust", ""),
            "capability": r.get("capability", ""),
            "risk": r.get("risk", ""),
            "decision": r.get("decision", ""),
            "ok": r.get("ok"),
            "exitCode": r.get("exitCode"),
            "gated": bool(r.get("gated", False)),
            "reason": str(r.get("reason", ""))[:240],
        }
        for r in records
    ]
    return {"counts": counts, "entries": entries, "total": len(entries)}


def collect_runs(limit: int = 25) -> dict:
    """Workflow run history from memory/workflows, including gauntlet gate verdicts."""
    if not RUNS_DIR.exists():
        return {"runs": [], "gates": [], "note": "no runs directory yet"}
    files = sorted(
        (p for p in RUNS_DIR.rglob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:limit]
    runs = []
    gates: list[dict] = []
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        stages = data.get("stages", data.get("results", []))
        run_id = data.get("runId", data.get("run_id", p.stem))
        runs.append({
            "id": run_id,
            "workflow": data.get("workflow", data.get("name", p.parent.name)),
            "engine": data.get("engine", "workflow/legacy"),
            "status": data.get("status", "unknown"),
            "reason": str(data.get("reason", ""))[:200],
            "startedAt": data.get("startedAt", data.get("started_at", "")),
            "stageCount": len(stages) if isinstance(stages, (list, dict)) else 0,
            "costUsd": data.get("costUsd", data.get("cost_usd")),
            "gateCount": len(data.get("gates") or []),
            "forcedCount": sum(1 for x in (data.get("gates") or []) if x.get("forced")),
            "file": str(p.relative_to(ROOT)).replace("\\", "/"),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                             .isoformat(timespec="seconds"),
        })
        # Gate verdicts are the whole point of the gauntlet engine: without this
        # the UI shows "it failed" instead of "it failed THIS way, so it rerouted".
        for gate in (data.get("gates") or []):
            gates.append({**gate, "run": run_id})
    return {"runs": runs, "gates": gates[:120]}


def collect_memory(limit: int = 20) -> dict:
    """Memory stats plus the most recent entries."""
    info: dict = {"megamindKb": 0, "episodicCount": 0, "recent": [], "byType": {}}
    if MEGAMIND_DB.exists():
        info["megamindKb"] = round(MEGAMIND_DB.stat().st_size / 1024, 1)
        try:
            con = sqlite3.connect(f"file:{MEGAMIND_DB}?mode=ro", uri=True, timeout=3)
            try:
                tables = {
                    r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                    )
                }
                target = next(
                    (t for t in ("memories", "memory", "entries", "megamind") if t in tables),
                    None,
                )
                if target:
                    cols = {r[1] for r in con.execute(f"PRAGMA table_info({target})")}
                    if "type" in cols:
                        info["byType"] = {
                            (row[0] or "unknown"): row[1]
                            for row in con.execute(
                                f"SELECT type, COUNT(*) FROM {target} GROUP BY type"
                            )
                        }
                    info["megamindRows"] = con.execute(
                        f"SELECT COUNT(*) FROM {target}"
                    ).fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error as exc:
            info["dbError"] = str(exc)[:200]

    entries = _tail_jsonl(MEMORY_JSONL, limit)
    info["episodicCount"] = sum(1 for _ in _safe_read_text(MEMORY_JSONL, 4_000_000).splitlines() if _.strip())
    info["recent"] = [
        {
            "type": e.get("type", ""),
            "topic": e.get("topic", ""),
            "text": str(e.get("text", ""))[:300],
            "ts": e.get("ts", e.get("timestamp", "")),
            "tags": e.get("tags", []),
        }
        for e in entries
    ]
    info["graph"] = collect_graph()
    return info


def collect_graph(limit: int = 12) -> dict:
    """Bi-temporal memory graph: what is true now, and what was superseded.

    Read-only and defensive: the graph tables may not exist yet on an older clone,
    which is a normal state rather than an error.
    """
    out: dict = {"available": False, "facts": 0, "liveFacts": 0, "supersededFacts": 0,
                 "entities": 0, "episodes": 0, "byKind": {}, "current": [], "changed": []}
    if not MEGAMIND_DB.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{MEGAMIND_DB}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        out["error"] = str(exc)[:200]
        return out
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"mg_fact", "mg_entity", "mg_episode"} <= tables:
            return out  # graph not initialised yet
        out["available"] = True

        def scalar(sql: str) -> int:
            return int(con.execute(sql).fetchone()[0])

        out["facts"] = scalar("SELECT COUNT(*) FROM mg_fact")
        out["liveFacts"] = scalar("SELECT COUNT(*) FROM mg_fact WHERE t_invalid IS NULL")
        out["supersededFacts"] = scalar("SELECT COUNT(*) FROM mg_fact WHERE t_invalid IS NOT NULL")
        out["entities"] = scalar("SELECT COUNT(*) FROM mg_entity")
        out["episodes"] = scalar("SELECT COUNT(*) FROM mg_episode")
        out["byKind"] = {r["kind"]: r["n"] for r in con.execute(
            "SELECT kind, COUNT(*) AS n FROM mg_fact GROUP BY kind ORDER BY n DESC")}
        out["current"] = [
            {"kind": r["kind"], "subject": r["subject"], "predicate": r["predicate"],
             "statement": (r["statement"] or "")[:220], "episodeId": r["episode_id"]}
            for r in con.execute(
                "SELECT f.kind, f.predicate, f.statement, f.episode_id, e.name AS subject "
                "FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
                "WHERE f.t_invalid IS NULL ORDER BY f.t_valid DESC LIMIT ?", (limit,))
        ]
        # Superseded facts are the payoff: proof memory can be corrected.
        out["changed"] = [
            {"subject": r["subject"], "predicate": r["predicate"],
             "was": (r["statement"] or "")[:160], "supersededBy": r["superseded_by"],
             "invalidatedAt": r["t_invalid"]}
            for r in con.execute(
                "SELECT f.predicate, f.statement, f.superseded_by, f.t_invalid, e.name AS subject "
                "FROM mg_fact f JOIN mg_entity e ON e.id=f.subject_id "
                "WHERE f.t_invalid IS NOT NULL ORDER BY f.t_invalid DESC LIMIT ?", (limit,))
        ]
    except sqlite3.Error as exc:
        out["error"] = str(exc)[:200]
    finally:
        con.close()
    return out


def collect_approvals() -> dict:
    """The Approvals List and backlog from memory/todo.md, plus parked runs.

    A run parked at an ``approval`` node is a *live* decision waiting on the
    Owner - more urgent than a note in a markdown file, so it belongs here.
    """
    text = _safe_read_text(TODO_PATH)
    items = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^[-*]\s*\[( |x|X|~|!)\]\s*(.+)$", s)
        if not m:
            continue
        mark, body = m.group(1), m.group(2).strip()
        state = {"x": "done", "X": "done", "~": "in-progress", "!": "blocked"}.get(mark, "pending")
        low = body.lower()
        if "needs-owner" in low or "needs owner" in low or "approve" in low:
            state = "blocked" if state == "pending" else state
        items.append({"state": state, "text": body[:300]})
    return {
        "items": items,
        "pending": sum(1 for i in items if i["state"] == "pending"),
        "blocked": sum(1 for i in items if i["state"] == "blocked"),
        "inProgress": sum(1 for i in items if i["state"] == "in-progress"),
        "parkedRuns": collect_parked_runs(),
    }


def collect_parked_runs(limit: int = 20) -> list[dict]:
    """Gauntlet runs that stopped without finishing and can be resumed."""
    if not RUNS_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{RUNS_DB}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gx_run" not in tables:
            return []
        rows = con.execute(
            "SELECT run_id, workflow, task, status, reason, updated_ts,"
            " (SELECT MAX(superstep) FROM gx_checkpoint c WHERE c.run_id=gx_run.run_id) AS steps"
            " FROM gx_run WHERE status IN ('running','interrupted')"
            " ORDER BY updated_ts DESC LIMIT ?", (limit,),
        )
        return [
            {
                "runId": r["run_id"], "workflow": r["workflow"],
                "task": (r["task"] or "")[:160], "status": r["status"],
                "reason": (r["reason"] or "")[:220], "steps": r["steps"] or 0,
                "updatedAt": datetime.fromtimestamp(r["updated_ts"], tz=timezone.utc)
                                     .isoformat(timespec="seconds"),
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def collect_surfaces() -> dict:
    """Which of the Owner's entry points are actually present/reachable."""
    ultron_repo = Path("C:/projects/ultron-cli")
    return {
        "harness": {"present": POLICY_PATH.exists(), "path": "harness.cmd"},
        "ultronBridge": {
            "present": (ROOT / "scripts" / "ultron.py").exists(),
            "path": "scripts/ultron.py",
        },
        "ultronCli": {
            "present": (ultron_repo / "package.json").exists(),
            "path": str(ultron_repo),
        },
        "gauntlet": {
            "present": (ROOT / "scripts" / "gauntlet.py").exists(),
            "path": "scripts/gauntlet.py",
        },
        "workflowEngine": {
            "present": (ROOT / "scripts" / "workflow.py").exists(),
            "path": "scripts/workflow.py",
        },
        "mcp": {"present": (ROOT / "mcp").exists(), "path": "mcp/"},
    }


def collect_all() -> dict:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(ROOT),
        "health": collect_health(),
        "policy": collect_policy(),
        "audit": collect_audit(),
        "runs": collect_runs(),
        "memory": collect_memory(),
        "approvals": collect_approvals(),
        "surfaces": collect_surfaces(),
    }


# ----------------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "AlfredDashboard/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if os.environ.get("ALFRED_DASHBOARD_VERBOSE"):
            super().log_message(fmt, *args)

    # -- security ---------------------------------------------------------
    def _authorized(self, query: dict) -> bool:
        supplied = (query.get("t", [""])[0]
                    or self.headers.get("X-Alfred-Token", ""))
        return secrets.compare_digest(supplied, TOKEN)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This UI renders local data; lock the browser down hard.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, indent=2).encode("utf-8"),
                   "application/json; charset=utf-8")

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if not self._authorized(query):
            self._json(HTTPStatus.UNAUTHORIZED, {
                "error": "unauthorized",
                "detail": "Append ?t=<token> (the token is printed when the server starts).",
            })
            return

        try:
            if route == "/":
                self._send(HTTPStatus.OK, PAGE.encode("utf-8"),
                           "text/html; charset=utf-8")
            elif route == "/api/all":
                self._json(HTTPStatus.OK, collect_all())
            elif route == "/api/health":
                self._json(HTTPStatus.OK, collect_health())
            elif route == "/api/policy":
                self._json(HTTPStatus.OK, collect_policy())
            elif route == "/api/audit":
                self._json(HTTPStatus.OK, collect_audit())
            elif route == "/api/runs":
                self._json(HTTPStatus.OK, collect_runs())
            elif route == "/api/memory":
                self._json(HTTPStatus.OK, collect_memory())
            elif route == "/api/approvals":
                self._json(HTTPStatus.OK, collect_approvals())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "no such route"})
        except PermissionError as exc:
            self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never take the UI down
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"error": type(exc).__name__, "detail": str(exc)[:400]})

    def do_POST(self) -> None:  # noqa: N802
        # v1 is deliberately read-only. See the module docstring.
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {
            "error": "read-only",
            "detail": "The dashboard observes; it never executes. Use harness.cmd for actions.",
        })


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alfred - Control Surface</title>
<style>
:root{
  --bg:#0b0d10; --panel:#12161b; --panel2:#171c22; --line:#232a33;
  --fg:#e6edf3; --dim:#8b98a5; --accent:#4da3ff; --ok:#3fb950;
  --warn:#d29922; --bad:#f85149; --mono:ui-monospace,"Cascadia Code",Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:14px;padding:14px 20px;
  border-bottom:1px solid var(--line);background:var(--panel);
  position:sticky;top:0;z-index:10;flex-wrap:wrap}
h1{font-size:16px;margin:0;letter-spacing:.3px}
h1 span{color:var(--dim);font-weight:400}
.badge{font:600 11px/1 var(--mono);padding:5px 9px;border-radius:999px;
  border:1px solid var(--line);color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
.badge.ok{color:var(--ok);border-color:#1d4429;background:#0d2416}
.badge.bad{color:var(--bad);border-color:#5c2321;background:#2a1211}
.badge.warn{color:var(--warn);border-color:#5a4413;background:#241d0d}
.spacer{flex:1}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  padding:7px 13px;border-radius:7px;cursor:pointer;font-size:13px}
button:hover{border-color:var(--accent);color:var(--accent)}
nav{display:flex;gap:4px;padding:10px 20px 0;flex-wrap:wrap}
nav button{border-radius:7px 7px 0 0;border-bottom:none;opacity:.6}
nav button.active{opacity:1;color:var(--accent);border-color:var(--line)}
main{padding:16px 20px 60px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:15px}
.card h3{margin:0 0 10px;font-size:11px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.7px;font-weight:600}
.metric{font:600 27px/1.1 var(--mono)}
.sub{color:var(--dim);font-size:12px;margin-top:5px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  position:sticky;top:0;background:var(--panel)}
tr:hover td{background:var(--panel2)}
code,.mono{font-family:var(--mono);font-size:12px}
.pill{display:inline-block;font:600 10px/1 var(--mono);padding:4px 7px;
  border-radius:5px;border:1px solid var(--line);color:var(--dim);
  text-transform:uppercase;letter-spacing:.4px;white-space:nowrap}
.pill.executed,.pill.ok,.pill.done{color:var(--ok);border-color:#1d4429;background:#0d2416}
.pill.denied,.pill.blocked,.pill.fail{color:var(--bad);border-color:#5c2321;background:#2a1211}
.pill.gated,.pill.pending,.pill--warn{color:var(--warn);border-color:#5a4413;background:#241d0d}
.pill.untrusted{color:var(--bad);border-color:#5c2321}
.pill.high{color:var(--ok);border-color:#1d4429}
.pill.medium{color:var(--accent);border-color:#1d3a5c}
.muted{color:var(--dim)}
.hidden{display:none}
.banner{border-radius:9px;padding:13px 15px;margin-bottom:15px;font-size:13px;
  border:1px solid var(--bad);background:#2a1211;color:#ffb3ae}
.banner.info{border-color:var(--line);background:var(--panel2);color:var(--dim)}
.wrap{max-height:60vh;overflow:auto;border:1px solid var(--line);border-radius:9px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:5px 0;
  border-bottom:1px dashed var(--line)}
.kv:last-child{border:none}
.trunc{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
footer{padding:14px 20px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
</style></head><body>
<header>
  <h1>ALFRED <span>/ control surface</span></h1>
  <span id="hbadge" class="badge">checking…</span>
  <span id="pbadge" class="badge hidden"></span>
  <div class="spacer"></div>
  <span id="stamp" class="muted mono"></span>
  <button id="refresh">Refresh</button>
</header>
<nav id="tabs"></nav>
<main>
  <div id="alert"></div>
  <div id="view"></div>
</main>
<footer>
  Read-only by design - the dashboard observes, it never executes. Loopback only, token required.
  Actions go through <code>harness.cmd</code> so every side effect stays under the signed policy.
</footer>
<script>
const TOKEN = new URLSearchParams(location.search).get('t') || '';
const TABS = [
  ['overview','Overview'], ['audit','Audit trail'], ['policy','Capabilities'],
  ['runs','Workflow runs'], ['gates','Gates'], ['memory','Memory'], ['approvals','Approvals'],
];
let active = location.hash.slice(1) || 'overview';
let DATA = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const el = id => document.getElementById(id);

function drawTabs(){
  el('tabs').innerHTML = TABS.map(([k,label]) =>
    `<button data-tab="${k}" class="${k===active?'active':''}">${label}</button>`).join('');
  el('tabs').querySelectorAll('button').forEach(b =>
    b.onclick = () => { active = b.dataset.tab; location.hash = active; drawTabs(); render(); });
}

async function load(){
  el('stamp').textContent = 'loading…';
  try{
    const r = await fetch(`/api/all?t=${encodeURIComponent(TOKEN)}`);
    if(!r.ok){
      const e = await r.json().catch(()=>({}));
      throw new Error(e.detail || e.error || `HTTP ${r.status}`);
    }
    DATA = await r.json();
    el('alert').innerHTML = '';
  }catch(err){
    el('alert').innerHTML = `<div class="banner"><b>Cannot load data.</b> ${esc(err.message)}</div>`;
    el('stamp').textContent = 'error';
    return;
  }
  const h = DATA.health;
  const hb = el('hbadge');
  hb.textContent = h.harnessOk ? 'harness ok' : 'harness FAIL';
  hb.className = 'badge ' + (h.harnessOk ? 'ok' : 'bad');
  const pb = el('pbadge');
  pb.classList.remove('hidden');
  pb.textContent = h.denyByDefault ? 'deny by default' : 'DENY-BY-DEFAULT OFF';
  pb.className = 'badge ' + (h.denyByDefault ? 'ok' : 'bad');
  el('stamp').textContent = (DATA.generatedAt || '').replace('T',' ').replace('+00:00',' UTC');

  if(!h.harnessOk){
    el('alert').innerHTML = `<div class="banner"><b>The harness is refusing to run anything.</b>
      Exit ${esc(h.exitCode)}. <div class="mono" style="margin-top:8px">${esc(h.error)}</div></div>`;
  }
  render();
}

function card(title, metric, sub){
  return `<div class="card"><h3>${esc(title)}</h3>
    <div class="metric">${esc(metric)}</div><div class="sub">${sub||''}</div></div>`;
}
function table(cols, rows){
  if(!rows.length) return '<div class="card muted">Nothing recorded yet.</div>';
  return `<div class="wrap"><table><thead><tr>${
    cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${
    rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
const pill = (v, cls) => `<span class="pill ${cls||String(v||'').toLowerCase()}">${esc(v)}</span>`;

function render(){
  if(!DATA) return;
  const v = el('view');
  const {health, policy, audit, runs, memory, approvals, surfaces} = DATA;

  if(active === 'overview'){
    const s = Object.entries(surfaces).map(([k,o]) =>
      `<div class="kv"><span>${esc(k)}</span>${
        o.present ? pill('present','ok') : pill('missing','denied')}</div>`).join('');
    const callers = Object.entries(policy.callers||{}).map(([n,c]) =>
      `<div class="kv"><span>${esc(n)} ${pill(c.trust)}</span>
        <span class="mono">${c.allowed.length} allowed / ${c.deniedCount} denied</span></div>`).join('');
    v.innerHTML = `<div class="grid">
      ${card('Capabilities', health.capabilityCount ?? '-', `${(health.gated||[]).length} gated`)}
      ${card('Audit events', audit.total, `${audit.counts.denied} denied · ${audit.counts.executed} executed`)}
      ${card('Workflow runs', (runs.runs||[]).length, 'most recent first')}
      ${card('Memory', memory.episodicCount, `${memory.megamindKb} KB megamind`)}
      ${card('Needs owner', approvals.blocked, `${approvals.pending} pending`)}
    </div>
    <div class="grid" style="margin-top:14px">
      <div class="card"><h3>Callers &amp; trust</h3>${callers||'<span class="muted">none</span>'}</div>
      <div class="card"><h3>Surfaces</h3>${s}</div>
    </div>`;
  }

  if(active === 'audit'){
    v.innerHTML = table(['When','Caller','Capability','Risk','Decision','Exit'],
      audit.entries.map(e => [
        `<span class="mono">${esc(e.ts)}</span>`,
        `${esc(e.caller)} ${pill(e.trust)}`,
        `<code>${esc(e.capability)}</code>${e.gated?' '+pill('gated'):''}`,
        `<span class="muted">${esc(e.risk)}</span>`,
        pill(e.decision) + (e.reason?`<div class="muted trunc">${esc(e.reason)}</div>`:''),
        e.exitCode===undefined||e.exitCode===null ? '<span class="muted">-</span>'
          : pill(e.exitCode===0?'ok':'fail', e.exitCode===0?'ok':'fail'),
      ]));
  }

  if(active === 'policy'){
    v.innerHTML = table(['Capability','Risk','Gated','Allowed for','Description'],
      Object.entries(policy.capabilities||{}).map(([name,c]) => {
        const who = Object.entries(policy.callers||{})
          .filter(([,cc]) => cc.allowed.includes(name)).map(([n]) => n);
        return [
          `<code>${esc(name)}</code>`,
          `<span class="muted">${esc(c.risk)}</span>`,
          c.gated ? pill('gated') : '<span class="muted">-</span>',
          who.length ? who.map(n=>pill(n,'')).join(' ') : pill('nobody','denied'),
          `<span class="muted">${esc(c.description)}</span>`,
        ];
      }));
  }

  if(active === 'runs'){
    v.innerHTML = (runs.runs||[]).length
      ? table(['Run','Workflow','Engine','Status','Stages','Gates','Cost','Updated'],
          runs.runs.map(r => [
            `<code>${esc(r.id)}</code>`, esc(r.workflow),
            `<span class="muted">${esc(r.engine)}</span>`,
            pill(r.status) + (r.reason?`<div class="muted trunc">${esc(r.reason)}</div>`:''),
            `<span class="mono">${esc(r.stageCount)}</span>`,
            r.gateCount ? `<span class="mono">${esc(r.gateCount)}</span>` +
              (r.forcedCount?' '+pill(r.forcedCount+' forced','gated'):'') : '<span class="muted">-</span>',
            `<span class="mono">${r.costUsd==null?'-':'$'+esc(r.costUsd)}</span>`,
            `<span class="mono">${esc(r.mtime)}</span>`,
          ]))
      : `<div class="banner info">No workflow runs recorded yet.
         Try <code>python scripts/gauntlet.py run workflows/feature-gated.json --save</code>.</div>`;
  }

  if(active === 'gates'){
    const g = runs.gates||[];
    const forced = g.filter(x=>x.forced).length;
    v.innerHTML = `<div class="grid">
        ${card('Gate decisions', g.length, 'across recent runs')}
        ${card('Forced routes', forced, 'anti-thrash / no-progress engaged')}
        ${card('Aborts', g.filter(x=>x.verdict==='ABORT').length, 'stopped rather than spun')}
      </div>
      <div class="banner info" style="margin-top:14px">
        A <b>forced</b> route means the engine overrode the gate: the same failure
        recurred, or output stopped changing, so it changed approach instead of retrying.
      </div>
      ${g.length ? table(['Run','Gate','#','Verdict','Reasons','Action','Why'],
        g.map(x => [
          `<code>${esc(x.run)}</code>`, esc(x.node),
          `<span class="mono">${esc(x.iteration)}</span>`,
          pill(x.verdict||'?', x.verdict==='PASS'?'ok':(x.verdict==='ABORT'?'denied':'gated')),
          (x.reasons||[]).map(r=>`<code>${esc(r)}</code>`).join(' ')||'<span class="muted">-</span>',
          `${esc(x.action||'')}${x.target?` <span class="muted">-&gt;</span> ${esc(x.target)}`:''}${x.forced?' '+pill('forced','denied'):''}`,
          `<span class="muted trunc">${esc(x.routingReason||'')}</span>`,
        ])
      : '<div class="banner info">No gate decisions recorded yet.</div>'}`;
  }

  if(active === 'memory'){
    const types = Object.entries(memory.byType||{}).map(([t,n]) =>
      `<div class="kv"><span>${esc(t)}</span><span class="mono">${esc(n)}</span></div>`).join('');
    const g = memory.graph||{};
    const graphCards = g.available ? `<div class="grid" style="margin-top:14px">
        ${card('Facts (current)', g.liveFacts, `${esc(g.facts)} total`)}
        ${card('Superseded', g.supersededFacts, 'corrected, not deleted')}
        ${card('Entities', g.entities, `${esc(g.episodes)} episodes`)}
        <div class="card"><h3>Facts by kind</h3>${
          Object.entries(g.byKind||{}).map(([k,n]) =>
            `<div class="kv"><span>${esc(k)}</span><span class="mono">${esc(n)}</span></div>`).join('')
          || '<span class="muted">none</span>'}</div>
      </div>` : `<div class="banner info" style="margin-top:14px">
        Memory graph not initialised. Run <code>python scripts/memgraph.py init</code>
        then <code>backfill</code>.</div>`;

    const changed = (g.changed||[]).length ? `
      <h3 style="color:var(--dim);font-size:11px;letter-spacing:.7px;margin:18px 0 8px">
        WHAT CHANGED <span class="muted">- superseded facts, kept for history</span></h3>
      ${table(['Subject','Predicate','Was','Replaced by'], g.changed.map(x => [
        esc(x.subject), `<code>${esc(x.predicate)}</code>`,
        `<span class="muted">${esc(x.was)}</span>`,
        x.supersededBy?`<code>#${esc(x.supersededBy)}</code>`:'<span class="muted">retracted</span>',
      ]))}` : '';

    v.innerHTML = `<div class="grid">
        ${card('Episodic entries', memory.episodicCount, 'memory.jsonl')}
        ${card('Megamind', memory.megamindKb+' KB', (memory.megamindRows??'?')+' rows')}
        <div class="card"><h3>By type</h3>${types||'<span class="muted">n/a</span>'}</div>
      </div>
      ${graphCards}
      ${g.available ? `
        <h3 style="color:var(--dim);font-size:11px;letter-spacing:.7px;margin:18px 0 8px">
          TRUE NOW</h3>
        ${table(['Kind','Subject','Statement','Source'], (g.current||[]).map(x => [
          pill(x.kind,''), esc(x.subject),
          `<span class="muted">${esc(x.statement)}</span>`,
          `<code>ep:${esc(x.episodeId)}</code>`]))}` : ''}
      ${changed}
      <h3 style="color:var(--dim);font-size:11px;letter-spacing:.7px;margin:18px 0 8px">RECENT EPISODIC</h3>
      ${table(['Type','Topic','Text'], (memory.recent||[]).map(m => [
        pill(m.type,''), esc(m.topic), `<span class="muted">${esc(m.text)}</span>`]))}`;
  }

  if(active === 'approvals'){
    const parked = approvals.parkedRuns||[];
    const parkedBlock = parked.length ? `
      <div class="banner" style="border-color:var(--warn);background:#241d0d;color:#f0d58c">
        <b>${parked.length} run(s) waiting on you.</b> A parked run has already done
        its work and checkpointed - resuming costs nothing for what is finished.
      </div>
      ${table(['Run','Workflow','Status','Steps','Waiting on','Resume with'], parked.map(r => [
        `<code>${esc(r.runId)}</code>`, esc(r.workflow),
        pill(r.status === 'interrupted' ? 'pending' : r.status),
        `<span class="mono">${esc(r.steps)}</span>`,
        `<span class="muted">${esc(r.reason)}</span>`,
        `<code>gauntlet.py run &lt;spec&gt; --resume --run-id ${esc(r.runId)}</code>`,
      ]))}` : '';

    v.innerHTML = `<div class="grid">
        ${card('Needs owner', approvals.blocked, 'from memory/todo.md')}
        ${card('Pending', approvals.pending, 'backlog items')}
        ${card('Parked runs', parked.length, 'awaiting a decision')}
      </div>
      ${parkedBlock}
      <h3 style="color:var(--dim);font-size:11px;letter-spacing:.7px;margin:18px 0 8px">BACKLOG</h3>
      ${(approvals.items||[]).length
        ? table(['State','Item'], approvals.items.map(i => [pill(i.state), esc(i.text)]))
        : '<div class="banner info">Nothing on the approvals list.</div>'}`;
  }
}

el('refresh').onclick = load;
drawTabs(); load();
setInterval(load, 20000);
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dashboard",
        description="Alfred's local, read-only control surface (loopback + token).",
    )
    ap.add_argument("--port", type=int, default=0,
                    help="port to bind (default: an OS-assigned free port)")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")
    ap.add_argument("--check", action="store_true",
                    help="collect once, print JSON, and exit (no server)")
    args = ap.parse_args(argv)

    if args.check:
        print(json.dumps(collect_all(), indent=2))
        return 0

    # Loopback only. Never bind 0.0.0.0 - this serves the audit trail and memory.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"

    print("Alfred dashboard - read-only control surface")
    print(f"  URL   {url}")
    print("  bind  127.0.0.1 (loopback only) | auth: per-session token | read-only")
    print("  stop  Ctrl+C", flush=True)

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
