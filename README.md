# Alfred

> A personal, self-improving multi-agent AI system — with a **bespoke local coder you fine-tune and own**, a **graph engine with explicit gates**, **memory that can be corrected**, live web access, and an **offline voice**.

![Kiro](https://img.shields.io/badge/Built%20on-Kiro-000000?style=flat-square)
![Claude](https://img.shields.io/badge/Claude-Opus%20%2F%20Sonnet-D97757?style=flat-square)
![Local LLM](https://img.shields.io/badge/Local-Qwen2.5--Coder%207B-615CED?style=flat-square)
![QLoRA](https://img.shields.io/badge/Fine--tune-QLoRA%20(free)-EE4C2C?style=flat-square)
[![tests](https://github.com/tusharbeckham/Alfred/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharbeckham/Alfred/actions/workflows/ci.yml)
![Dependencies](https://img.shields.io/badge/runtime%20deps-none-3fb950?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![PowerShell](https://img.shields.io/badge/PowerShell-5391FE?style=flat-square&logo=powershell&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Voice](https://img.shields.io/badge/Voice-Piper%20TTS%20(offline)-8A2BE2?style=flat-square)

Alfred is a personal AI operating layer: a coordinated team of specialized agents that **code, manage a Windows PC, and run projects** — backed by a locally fine-tuned model, persistent memory, and web access. It runs on frontier models when it matters and a **free, offline local model** for everything routine.

Four surfaces drive it — the Kiro CLI, the [Ultron CLI](https://github.com/tusharbeckham/Ultron), local models via LM Studio, and Notion AI over MCP — and all four reach the same engine through the same **cryptographically signed** capability policy, landing in the same audit trail.

**Everything here is standard library only.** No pip install, no npm install, no CDN, no cloud account. It works offline on a fresh clone, and `python scripts/test_*.py` → **515 tests passing**.

---

## Highlights

- **One interactive console** — `alfred` is a terminal surface for the whole system: live subsystem status, graph runs rendered as **motion**, memory recall, the local model, and the harness policy chain shown control by control. Tab completion and persistent history, built on `msvcrt` rather than a `readline` dependency.
- **The Gauntlet — gates, not retry loops** — work advances only by passing an explicit gate that returns a *structured verdict*. Every rung of the degradation ladder is bounded, including a **code-independent** cap that a model cannot escape by renaming its failures. A live 7B gate defeated the naive per-code rule in exactly that way; §2.2 of the plan documents the fix.
- **Memory that can be corrected** — a bi-temporal graph over SQLite: facts carry *world time* and *system time*, so a changed preference **invalidates** the old value instead of coexisting with it forever. Nothing is deleted, provenance is mandatory, and recall is token-bounded.
- **A policy-gated automation harness** — one entrypoint (`harness.cmd`) guarded by a **signed** capability policy. Deny-by-default, per-caller allowlists, argv-only execution, append-only audit. An untrusted local model that edits the policy invalidates its signature and the harness refuses to run anything.
- **Crashes resume; approvals park** — state is checkpointed after every node, so a crash re-runs only the crashed node instead of re-billing completed LLM calls. A `kind: approval` node parks the run for the Owner and resumes without repeating finished work.
- **Bespoke local coder (offline, $0)** — a **Qwen2.5-Coder-7B fine-tuned via QLoRA** on a free cloud GPU, served through LM Studio. Gate quality is *measured*: `scripts/eval_gates.py` took it from **33% to 100%** on a fixture set by rewriting one prompt.
- **Hybrid routing on evidence** — one registry for LM Studio, Ollama, NVIDIA NIM, DeepSeek, FreeBuff and OpenRouter, with per-tier routing. Gates go to the cheapest fast tier; bulk work stays local and free. Unknown rates report *unknown*, never `$0`.
- **A true orchestrator at the top** — the `alfred` agent owns outcomes end-to-end: every agent in the registry is pre-trusted, so delegation never stalls on a permission prompt, while destructive/system/production actions stay hard-gated. Backed by `true-leadership` and `token-economy` doctrine plus always-on `resilience` steering that defines a 7-rung degradation ladder instead of a crash.
- **Multi-agent orchestration** — an overseer plus 4 tiers of specialized agents (manager, leader, architect, coder, tester, reviewer, researcher, debugger, devops, security, docs, data, ML, backend, cloud, SRE, frontend, and more) collaborating through DAG pipelines with loops and fan-in.
- **Live web access** — keyless search + page-fetch available to every agent (and to the local model when online).
- **Offline voice** — Alfred *speaks*: a local **neural text-to-speech voice (Piper)** with a Windows fallback. `ask`, `talk`, and `say`, all offline, no keys.
- **Eval-driven self-improvement** — prompts and skills are optimized against versioned eval suites with regression guards.
- **Safety-gated autonomy** — destructive, system, production, or secret-touching actions require explicit approval; unattended runs are sandboxed to project work.

---

## Architecture

**Cognition — the Brain System.** Every agent has a 6-layer stack: identity (system prompt), reasoning effort, always-on instincts (steering), on-demand skills, persistent memory, and lifecycle reflexes (hooks).

**Org chart (high level).**
```
Owner ─ Alfred (overseer)
          └─ Manager ─ Leader ─ Workers (coder, tester, reviewer, researcher,
                                          debugger, devops, security, docs, data,
                                          ML, backend, math, physics, …)
             plus meta-agents: evaluator, trainer, memory-curator, agent-builder
```

**Two kinds of "training."**
- The **orchestration layer** improves via *eval-driven prompt & skill optimization* — never model-weight training.
- The **local coder** is a *genuine QLoRA fine-tune* of an open model you own end-to-end.

---

## The harness — automating this machine safely

One entrypoint automates the PC, and **nothing runs unless a signed policy allows it for that caller**.

```powershell
harness verify                              # is the policy intact?
harness list --caller owner                 # what can I run?
harness run status                          # run a capability
harness run git-status --param path=C:\Alfred
harness run backup --approve                # gated: needs explicit approval
```

Five independent controls, because the threat being defended against is *a local model that
can edit files in this repo*:

| Control | Effect |
|---|---|
| **Signed policy** | `policy/harness-policy.json` is HMAC-SHA256 signed; the key lives in `secrets/` where every agent is denied access. Edit the policy without the key and the harness refuses to run **anything**. |
| **Deny by default** | Capabilities not in the policy cannot run, whoever asks. |
| **Per-caller allowlists** | `owner` (high) · `kiro-agent` (medium) · `local-model` (**untrusted**, 5 read-only diagnostics, token required) · `scheduled` (low). |
| **Argv-only execution** | No shell string is ever built. Params are type-validated, paths confined, enums exact-matched. Injection payloads are inert data. |
| **Audit trail** | Every attempt — executed, denied, or dry-run — appends to `memory/harness-audit.jsonl`. |

Verified by `python scripts/test_harness.py` → **25 tests**, including one that grants
`local-model` full capabilities and asserts the whole harness then fails closed.
Full documentation and threat model: [`docs/harness.md`](docs/harness.md).

## The console — one interactive surface

```
    _    _     _____ ____  _____ ____
   / \  | |   |  ___|  _ \| ____|  _ \
  / _ \ | |   | |_  | |_) |  _| | | | |
 / ___ \| |___|  _| |  _ <| |___| |_| |
/_/   \_\_____|_|   |_| \_\_____|____/

  ✓ harness       23 capabilities, 4 gated
  ✓ lm studio     alfred-coder-7b, text-embedding-nomic-embed-text-v1.5
  ✓ memory graph  58 facts, 59 embedded
  ✓ ultron        node + gauntlet parity
```

```powershell
alfred                          # interactive console
alfred status                   # one-shot: probe every subsystem
alfred run feature-gated "..."  # execute a graph with live motion
alfred resume <run-id> <node>   # continue a parked run, approving a gate
alfred ask "..."                # local model, with memory injected
alfred models                   # provider status (local, nvidia, deepseek, ...)
```

Graph runs are rendered as **motion** — the chain is redrawn in place as control moves through it, with gate verdicts and forced reroutes appearing on the edges:
```
  ● ✓ build          18263ms
  ● ✓ verify         25023ms
  ◆ ✓ release-gate   27584ms
      ├─ PASS → owner-approval
  ▲   owner-approval PARKED: awaiting the Owner's approval
```

That is deliberate: the engine is a graph with explicit gates, not a retry loop, so the display shows the route being taken rather than a scrolling log. Every command is also scriptable — the console runs one-shot when given arguments, and `alfred "write a function"` still goes to the local coder as it always did.

**The harness is a chain too.** `do <capability>` shows every policy control as it is checked, so a refusal is visible rather than a single silent no:

```
  ◆ ✓ caller     owner (trust=high)
  ◆ ✓ defined    risk=read
  ◆ ✓ allowlist  permitted for this caller
  ◆ ✗ gate       gated; needs explicit --approve
  DENIED 'backup' is gated. Re-run with --approve to confirm.
```

Every row is a real control inside `run_capability` — caller resolution, token auth, deny-by-default, the per-caller allowlist, the gate, parameter validation, argv construction, execution, audit. Nothing is drawn for decoration.

**Interactive editing** comes from `scripts/lineedit.py`, built on `msvcrt` because Python ships no `readline` on Windows and `pyreadline3` would be a dependency: tab completion (context-aware — `run <TAB>` offers specs, `do <TAB>` offers capabilities), up/down history persisted across sessions, `Ctrl+U/A/E`, and `Ctrl+C` cancels the line instead of killing the session. It falls back to `input()` whenever stdin or stdout is not a console, so piping still works.

Encoding is handled explicitly: every glyph has an ASCII fallback chosen from the real stdout encoding, because a cp1252 console raises `UnicodeEncodeError` on box-drawing characters. `NO_COLOR` is honoured, and animation degrades to one streamed line per event when stdout is not a TTY (so logs and CI stay readable).

### Local models

`alfred lms up` starts LM Studio's server and loads a model **discovered from disk**, not hardcoded, at `4096` context × `1` parallel slot. Those defaults are deliberate: LM Studio's own `8192 × 4` KV cache took this machine to 2.4 GB free and turned an 8 s call into a 120 s timeout. Reloading lean freed ~5.7 GB and brought the same call back to 4.8 s — and one slot is right anyway, because the graph engine runs nodes sequentially. `lms lean-reload` fixes accumulated cache; `lms models` shows what is downloaded and which would be loaded.

A run **refuses to start** if the model cannot answer a 5-token call, with the estimated cost of proceeding — 46 s of honest refusal beats five minutes of timeouts. `--anyway` overrides. There is deliberately no silent fallback to a stub, because fake work is worse than a clear no.

Gate quality is measured, not assumed: `python scripts/eval_gates.py` scores the local model against unambiguous fixtures. Rewriting the gate prompt with few-shot examples and an explicit "ABORT is rare" rule moved a 7B model from **33% to 100%** on that eval; its failure mode was treating every problem as fatal.

### Models and keys

`scripts/providers.py` is one registry for every OpenAI-compatible endpoint — LM Studio and Ollama locally, plus NVIDIA NIM, DeepSeek and OpenRouter:

```powershell
python scripts/providers.py list                      # what is usable right now
python scripts/providers.py set-key DEEPSEEK_API_KEY   # input hidden, stored in secrets/
python scripts/providers.py pick                       # cheapest reachable provider
```

Keys are read from the environment first, then `secrets/models.json` (git-ignored, and on the dashboard's forbidden-path list). They are **never printed** — every display path shows a masked fingerprint like `set (...1234)`. Cost estimates return *unknown* rather than zero for a hosted provider whose rates are not filled in, because silently reporting `$0` for billable calls is worse than admitting ignorance.

## The Gauntlet — gates instead of retry loops

Work advances only by passing an explicit **gate**, and every gate returns a *structured verdict* (`PASS` / `RETRY` / `REROUTE` / `ESCALATE` / `ABORT`) rather than a matched trigger string. The **engine** owns what happens next, not the model.

```powershell
python scripts/gauntlet.py validate workflows/feature-gated.json
python scripts/gauntlet.py graph    workflows/feature-gated.json
python scripts/gauntlet.py run      workflows/feature-gated.json --task "add validation" --save
```

Why it matters: a retry loop cannot tell "tests failed" from "the model refused", so it applies the same remedy forever. Here every rung of the degradation ladder is **bounded**, so a stuck approach is replaced rather than repeated:

```
RETRY x2  ->  REROUTE x2  ->  ESCALATE x1  ->  ABORT (partial result + reason)
 fix, fix     replan            deep-review     stop, honestly
```

Three guarantees hold regardless of what a gate asks for:
- A third `RETRY` carrying the **same reason code** is impossible — it becomes `REROUTE`.
- A node that produced **output it already produced** is rerouted, never retried (two identical artifacts is by definition no progress).
- A gate may reject at most **4 times in total**, whatever it calls the failures. This one matters most: a live 7B gate defeated the per-code rule by inventing a new reason code each attempt — 40 node runs, **zero** forced reroutes. A guarantee keyed on a value the model chooses is not a guarantee, so total attempts are bounded too. Unrecognised codes are also folded into `OTHER` so repeated failures converge on one counter.

An unreadable gate **fails closed** (`ABORT`), never open — a broken gate is not a passed gate. Escalation is capped hardest because it is the rung that spends premium credits. Measured on a permanently failing gate: **14 node runs then a clean abort**, instead of spinning until the budget dies.

Verified by `python scripts/test_gauntlet.py` → **129 tests**. Gate verdicts and forced routes appear in the dashboard's **Gates** tab, so you can see what the engine refused to do and why.

### Crashes resume; approvals park

A crash used to mean re-billing every completed LLM call. State is now written after **every node**, into its own `memory/gauntlet-runs.db` (run state gets pruned; memory does not).

```powershell
python scripts/gauntlet.py run workflows/deploy-gated.json --checkpoint --run-id ship-1
python scripts/gauntlet.py runs --resumable          # what is waiting
python scripts/gauntlet.py run workflows/deploy-gated.json --resume --run-id ship-1 --approve owner-approval
python scripts/gauntlet.py prune --days 14           # TTL; unfinished runs are kept
```

A node with `kind: approval` **parks** the run instead of blocking a process for hours — status `interrupted`, which is *not* a failure and does *not* trigger rollback. You approve out of band and resume; work already done is not repeated. Verified on `deploy-gated.json`: parked after 3 nodes, resumed, and only `deploy` executed.

Rollback goes **through the signed policy** (`--compensate`): every compensator must be a declared harness capability, so an undo inherits deny-by-default, argv-only execution and the audit trail. The engine cannot invent a destructive action, and `local-model` cannot roll anything back.

## One spec, two engines — the Ultron bridge

Alfred and Ultron stay separate on purpose (Python vs Node, different trust models). The contract between them is the `gauntlet/v1` spec, and it is enforced on **both** sides:

| | Alfred harness | Ultron CLI |
|---|---|---|
| Owns | policy, safety, memory, this machine | model access, the REPL, portable pipelines |
| Engine | `scripts/gauntlet.py` | `src/gauntlet.mjs` |
| Trust | signed policy, per-caller allowlists | permission profiles |

Why mirror the router instead of just the schema: the anti-thrash rule is *structural*. If Ultron ran the same graph with a plain retry loop, then **where** you ran a spec would silently change what it was allowed to do, and the guarantee would belong to the runtime rather than the spec. So both engines carry the same bounds, and a parity test fails the build if they drift:

```powershell
python scripts/test_ultron_parity.py     # 13 tests: same verdict, same route, same bounds
harness run ultron-gauntlet-check --param spec=workflows/feature-gated.json
harness run ultron-pipeline --param pipeline=feature --param task="..." --approve
```

`ultron-pipeline` is **gated** — it is the one capability that can spend real money at scale, so it needs an explicit `--approve`. The untrusted `local-model` gets **neither** Ultron capability: not the pipeline (money, agents) and not even the read path, because shelling into another runtime is a larger surface than it is trusted with.

This is what joins the four surfaces. Kiro CLI, Ultron CLI, local models and Notion AI over MCP all reach the same engine through the same signed policy, and every call lands in the same audit trail.

## Memory that can be corrected

An append-only log recalls contradictions forever. Ask it "which model does the Owner prefer?" a month after he switched and it will happily return the old answer. The memory graph fixes that with **two independent timelines** (the model Zep/Graphiti describe):

- **world time** — `t_valid` / `t_invalid`: when a fact was true
- **system time** — `t_created` / `t_expired`: when Alfred learned it

```powershell
python scripts/memgraph.py assert --subject owner --predicate prefers-coder `
    --object "deepseek flash" --kind preference --statement "..."   # invalidates the old value
python scripts/memgraph.py current --subject owner    # what is true NOW
python scripts/memgraph.py history --subject owner --predicate prefers-coder   # what CHANGED
python scripts/memgraph.py recall  -q "which coder model does the owner prefer"
```

Nothing is ever deleted — a superseded fact keeps its `t_valid`, gains a `t_invalid`, and records `superseded_by`, so both questions stay answerable. **Provenance is mandatory:** every fact cites the episode that produced it, and `graph-doctor` fails if one doesn't. A fact without a source would be fabrication.

| Property | How |
|---|---|
| **Additive** | New `mg_*` tables inside the existing `megamind.db`. `megamind.py` keeps working untouched; 52 existing memories were backfilled with real provenance. |
| **Zero dependencies** | Stdlib only: FTS5 BM25 + cosine over float32 BLOBs + a depth-capped recursive CTE, fused with reciprocal rank fusion. `sqlite-vec` was **deferred, not adopted** — recall must not require a pip install, and vectors are stored in its exact blob layout so it can be added later as a pure accelerator. |
| **Works offline** | Embeddings are optional. With LM Studio down, keyword + traversal still answer. |
| **Token-bounded** | Recall returns a budgeted block, never "everything relevant". If matches exist but don't fit, it returns the best one *clipped* and says so — reporting "nothing relevant" while relevant facts exist is lying by omission. |

Available to every surface as policy-gated capabilities: `graph-recall`, `graph-current`, `graph-history`, `graph-assert`, `graph-doctor`. The untrusted local model gets the **read** ones only — a model that can rewrite memory can rewrite its own instructions on the next recall.

Verified by `python scripts/test_memgraph.py` → **59 tests**. Current facts and a **what changed** history appear in the dashboard's Memory tab.

## The dashboard — one place to see everything

Alfred is driven from several text surfaces (Kiro CLI, Ultron CLI, local models, Notion AI over MCP). None of them answers *"what is this system doing right now, and what is it refusing to do?"* The dashboard does.

```powershell
dashboard                 # open the UI on a free port
dashboard --port 7373     # pin the port
dashboard --check         # dump the whole snapshot as JSON (no server)
```

Six tabs: **health** (is the signed policy verifying?), **audit trail**, **capabilities** per caller, **workflow runs**, **memory**, and the **approvals list**.

Built to the same constraints as the rest of the system:

| Constraint | How |
|---|---|
| **Zero dependencies** | Python stdlib only — no pip, no npm, no CDN. Works offline on a fresh clone. |
| **Loopback only** | Binds `127.0.0.1`, never `0.0.0.0`. It can read the audit trail and memory, so it is not reachable from the network. |
| **Token gated** | A fresh session token is minted per start and required on every route, including the HTML page. |
| **Read-only** | `POST` returns 405. A browser that could trigger capabilities turns any XSS into code execution, so actions stay behind `harness.cmd` and the signed policy. |
| **Never serves secrets** | `secrets/` is on a forbidden-path list; a test asserts the signing key never appears in any response. |

Verified by `python scripts/test_dashboard.py` → **18 tests**, covering auth refusal, read-only enforcement, secret safety, and the CSP headers.

> Design rationale — including why **LangChain/LangGraph was rejected** (LangGraph Studio requires a LangSmith account and API key, which would put a cloud dependency in an offline-first system) and why **Ultron is updated rather than rebuilt** — is in [`docs/adr/001-control-surface-ui-langchain-ultron.md`](docs/adr/001-control-surface-ui-langchain-ultron.md).

## The local coder (offline & free)

- Runs an open coding model (**Qwen2.5-Coder-7B**, fine-tuned) in **LM Studio** at an OpenAI-compatible endpoint — no API keys, no per-token cost.
- **Fine-tune pipeline:** curate examples → build a chat-format dataset → **QLoRA on a free cloud GPU** → export a GGUF → load locally.
- **One-command use** from any terminal:
  ```powershell
  alfred "write a PowerShell function that returns the 5 largest files in a folder"
  ```
- **Measure it:** eval suites + a local scorer capture behavior before/after a fine-tune.

---

## Quick start

### From a fresh clone

Python 3.11+ and nothing else — no `pip install`, no npm, no cloud account, works
offline.

```powershell
git clone https://github.com/tusharbeckham/Alfred.git
cd Alfred
python scripts/harness.py sign     # generate THIS clone's signing key
alfred status                      # probe every subsystem
Get-ChildItem scripts/test_*.py | ForEach-Object { python $_.FullName }   # 515 tests
```

Sign first, and the reason is the whole design in one command: the key that signs
the capability policy lives in `secrets/` and is **never** committed, so a fresh
clone has a policy and no key — and the harness fails closed, refusing to run
anything at all until you make a key of your own. There is no shared secret to
leak, and nothing to trust from me.

`alfred status` is honest about what is missing. LM Studio, Piper and the Kiro CLI
are all optional; every one of them degrades to a clear message instead of a crash.

### With the agent layer

```powershell
# Talk to your assistant / production manager
kiro-cli chat --agent alfred-manager

# Or let the orchestrator run a task end-to-end
kiro-cli chat --agent alfred-leader "Build a Python CLI word-counter with tests"

# Talk to Alfred out loud (offline neural voice)
ask "what's the fastest way to find big files on my PC?"   # one spoken answer
talk                                                         # a back-and-forth voice chat
say "good evening, sir"                                      # speak any text

# Or hit the free local coder directly
alfred "add input validation to this function" 
```

---

## Layout

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Top-level governance (mission, org chart, safety) |
| `.kiro/agents/` | Agent configurations |
| `.kiro/brains/` | Per-agent cognition (identity, memory, skills, reflexes) |
| `.kiro/steering/` | Always-on rules (identity, safety, resilience, token-budget, routing, memory, web) |
| `.kiro/skills/` | On-demand domain expertise (incl. `true-leadership`, `token-economy`) |
| `alfred.cmd` · `scripts/console.py` | The interactive console: status, graph motion, recall, local model |
| `scripts/providers.py` | Model provider registry (LM Studio, Ollama, NVIDIA, DeepSeek, OpenRouter) |
| `scripts/brand.py` | Shared logo, colours and encoding-safe glyphs |
| `harness.cmd` · `policy/` | The policy-gated automation harness and its signed capability policy |
| `dashboard.cmd` · `scripts/dashboard.py` | The local, read-only control surface (loopback + token) |
| `scripts/` | Automation: **harness**, **workflow engine**, security tools, local coder, memory, web, voice (TTS), fine-tune builder, CI, training |
| `workflows/` | Declarative multi-agent DAG workflow specs (run by `scripts/workflow.py`) |
| `evals/` | Eval datasets + rubrics |
| `docs/` | Setup and workflow guides (incl. [`harness.md`](docs/harness.md), [`graph-engineering-plan.md`](docs/graph-engineering-plan.md)) |
| `notebooks/` | Fine-tune notebook |

> Personal data — the memory trail, fine-tune datasets, eval outputs, and secrets — is kept **local-only** and git-ignored by design.

---

## What this is not

Worth saying plainly, because the feature list above is long:

- **Not a model.** Alfred is an orchestration layer over other people's models.
  "Training" here means eval-driven prompt and skill optimization, with exactly one
  exception: the local coder is a genuine QLoRA fine-tune of an open model.
- **Not a product.** It is built around one person's machine, one Windows install,
  and one set of preferences. Paths are absolute in places. It will not survive
  first contact with your setup unread.
- **Not audited.** The harness threat model in [`docs/harness.md`](docs/harness.md)
  is my own reasoning, tested by 25 tests I also wrote. Read it before trusting it
  with anything that matters.
- **Not multi-user.** Every trust decision assumes a single Owner at the keyboard.

## Contributing, security, licence

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to run it, the tests, and the house
  rules (standard library only; fail loudly; comments explain *why*).
- [`SECURITY.md`](SECURITY.md) — what this software can do to a machine, what is
  deliberately absent from the repository, and how to report a vulnerability
  privately.
- [MIT](LICENSE).

---

## Tech

Kiro · Claude (Opus / Sonnet) · LM Studio · Qwen2.5-Coder · Unsloth · QLoRA · local embeddings (RAG-style memory) · Piper TTS · MCP · PowerShell · Python.
