# ADR 001 — The control surface: UI, LangChain, and the Ultron question

> **Status:** accepted, partially implemented (2026-08-12).
> Answers three questions the Owner asked: should we adopt LangChain for a better
> UI, should we rebuild or update Ultron, and what the UX should actually be.
> Every external claim carries a source URL.

---

## Context

Alfred is driven from four places today, and that number is growing:

| Surface | What it is | State |
|---|---|---|
| **Kiro CLI** | frontier-model agents (Opus/Sonnet) | working |
| **Ultron CLI** (`C:\projects\ultron-cli`) | Node, zero-dep, own provider ladder + Notion OAuth | working, ~100 KB of `.mjs` |
| **Local models** (LM Studio) | free/offline coding tier | working, currently offline |
| **Notion AI over MCP** | workspace data + tools | OAuth implemented in Ultron |

All four are **text surfaces**. None of them answers *"what is this system doing
right now, and what is it refusing to do?"* That was the actual gap — not a
missing framework.

### The blocker found first

Before any of this could matter, the harness was **completely bricked**. The HMAC
was computed over the policy's raw bytes, and `core.autocrlf=true` rewrote LF to
CRLF on checkout, so the signature stopped verifying and **every capability
failed closed** (`exit 2 EXIT_POLICY`) with a silent error under PowerShell
redirection. The Owner could not have used the harness from *any* of the four
surfaces. Fixed before anything else; see §4.

---

## Decision 1 — Do NOT adopt LangChain or LangGraph

The Owner asked for "langchain as well?? for a better ui". Two separate things
are tangled here, and both answers are no.

**LangChain is not a UI framework.** It is an agent/LLM orchestration library.
The nearest thing to a GUI in that ecosystem is **LangSmith/LangGraph Studio**,
and its documented requirements disqualify it:

- *"Studio **requires a LangSmith API key** to connect your local agent."*
- *"A LangSmith account: Sign up (for free) or log in at smith.langchain.com."*
- Install is `pip install --upgrade "langgraph-cli[inmem]"`.

(source: https://docs.langchain.com/oss/python/langgraph/studio)

So adopting it would mean: a **cloud account + API key** for a system whose stated
premise is offline-first and local-only, a new dependency tree, and — the real
cost — **rewriting Alfred's engine as a LangGraph `StateGraph`**, because Studio
only visualizes LangGraph agents. Alfred already has a validated DAG engine
(`scripts/workflow.py`, 59 passing tests) and a started gate engine
(`scripts/gauntlet.py`). Replacing working, owned, tested code with a framework
that adds a cloud dependency is a straight downgrade against every constraint the
Owner set — including "save credits", since LangChain saves none.

**What we do instead:** keep borrowing LangGraph's *ideas*, which are genuinely
good and already written into `docs/graph-engineering-plan.md` — supersteps,
conditional edges, durable checkpointers, a recursion cap. Ideas are free;
dependencies are not.

> If this is ever revisited, the trigger should be a concrete capability we cannot
> build cheaply — not the appeal of a framework.

## Decision 2 — Update Ultron; do not rebuild, do not merge

`C:\projects\ultron-cli` already contains the expensive, hard-to-rebuild parts:

| Module | Size | Why it matters |
|---|---|---|
| `providers.mjs` | 21.9 KB | the multi-provider model ladder |
| `subagents.mjs` | 13.0 KB | subagent orchestration |
| `notion-oauth.mjs` | 10.6 KB | OAuth 2.1 + PKCE, token encryption |
| `interactive.mjs` | 7.6 KB | the REPL |
| `mcp-http.mjs` / `mcp.mjs` | 6.7 KB | MCP transport |
| **`ui.mjs`** | **1.5 KB** | **the actual gap** |

Rebuilding that is weeks of work to arrive back where we are. And the ratio is
the whole story: ~100 KB of solid capability behind a **1.5 KB** presentation
layer. Ultron does not need replacing; it needs a face.

**They also must not be merged.** They have different jobs, and the split is a
feature:

| | Alfred harness | Ultron CLI |
|---|---|---|
| Owns | policy, safety, memory, this machine | model access, the REPL, portable pipelines |
| Language | Python | Node, zero-dep |
| Trust model | signed policy, per-caller allowlists | permission profiles |
| Runs | here, gated | anywhere |

The contract between them stays the shared graph spec (`gauntlet/v1`) already
specified in `docs/graph-engineering-plan.md` §4.

## Decision 3 — The UX is a local, read-only dashboard (built)

`scripts/dashboard.py` + `dashboard.cmd`. One page that answers "what is this
system doing?" across all four surfaces.

**Why a local web page and not a TUI or Electron:**

- A **browser** is the only UI toolkit already installed on every machine, needs
  no runtime, and renders well on a phone over loopback forwarding if ever needed.
- **Electron/Tauri** means a build step, hundreds of MB, and a dependency tree —
  against the zero-dep rule.
- A **TUI** (Textual/Ink) would be a fifth text surface, when the problem was
  that text surfaces do not give an at-a-glance answer.
- **Zero dependencies:** Python stdlib only (`http.server`, `json`, `sqlite3`).
  No pip, no npm, no CDN. Works on a fresh clone, offline.

### Security posture — deliberate, and tested

This process can read the audit trail, the policy and memory, so it is treated as
a security surface, not a toy:

| Control | Implementation | Test |
|---|---|---|
| Loopback only | binds `127.0.0.1`, never `0.0.0.0` | reviewed; server constructed with explicit host |
| Token gated | 32-char `secrets.token_urlsafe`, `compare_digest`, required on every route incl. the HTML page | `test_no_token_is_refused`, `test_wrong_token_is_refused`, `test_the_html_page_also_requires_the_token` |
| **Read-only** | `POST` returns 405; nothing is ever executed | `test_post_is_refused` |
| Secrets never served | `secrets/` on a forbidden-path list; `_safe_read_text` raises | `test_reading_from_secrets_is_refused`, `test_no_payload_leaks_the_signing_key` |
| Browser lockdown | CSP `default-src 'none'`, `connect-src 'self'`, nosniff, no-store, no-referrer | `test_security_headers_are_present`, `test_csp_forbids_remote_origins` |

**Read-only is the important one.** A dashboard that could trigger capabilities
turns any XSS or stray `fetch` into code execution on the Owner's machine. Actions
stay behind `harness.cmd` and the signed policy. Mutations are not "not done yet";
they are **deliberately out of scope** until the auth story is designed properly.

The dashboard also *verifies* rather than trusts: it shells out to
`harness.py verify` and shows a red banner if the harness is refusing to run —
which is exactly the failure that was live when this work started.

---

## 4. What was actually fixed along the way

| Bug | Impact | Fix |
|---|---|---|
| **Harness bricked by CRLF** | every capability denied, from every surface | `canonical_policy_bytes()` normalizes line endings at the single HMAC chokepoint. Content proven authentic by matching the *existing* signature over LF-normalized bytes — **no re-signing needed**, so no new trust was minted. + `.gitattributes` |
| Silent policy failure | `exit 2`, no message under PowerShell redirection | message confirmed present; diagnosis documented (use `cmd /c` to see harness stderr) |
| **megamind sync silently broken** | `-g ''` dropped by PowerShell → argparse failed → **every** memory write skipped the SQLite index, while printing "synced to megamind.db" | build the arg list conditionally; report real sync status |

The middle one is the honesty bug: it claimed success while failing. That is the
class of defect Alfred's own steering forbids, so it is now impossible to repeat
silently — the message reflects the actual exit code.

---

## Consequences

**Good**
- The harness works again from every surface, with a regression test pinning it.
- One place to see health, capabilities, audit trail, runs, memory and approvals.
- No new dependencies, no cloud account, no credits spent to render a UI.

**Accepted costs**
- The dashboard cannot *do* anything yet. Correct for now; revisit with a real
  auth design (origin checks + CSRF token + policy-gated allowlist).
- Ultron still has a 1.5 KB UI. Improving it is the next step, not this one.
- Run history is empty, so the Runs tab is unproven against real data — it
  degrades to an explanatory empty state rather than an error.

## Next

1. Ultron's UI (`ui.mjs`) — the remaining half of the Owner's ask.
2. Wire `gauntlet.py` into `workflow.py` (plan phases 1–2) and surface verdicts
   in the dashboard, so gates become visible instead of inferred.
3. Memory graph (plan phases 6–7) — `sqlite-vec` + bi-temporal facts.

## Sources

- LangGraph/LangSmith Studio requirements: https://docs.langchain.com/oss/python/langgraph/studio
- Prior art and the graph plan of record: `docs/graph-engineering-plan.md`
