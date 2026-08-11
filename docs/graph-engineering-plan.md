# Graph Engineering — the Alfred Harness Plan

> **Status:** design, not yet implemented. This document is the plan of record for evolving the
> Alfred harness from *bounded loops* to a **graph-native execution and memory system**, and for
> connecting it to Ultron across local and cloud models.
>
> Researched and written 2026-08-12. Every external claim carries a source URL. Where something
> is unverified or contested, it says so.

---

## 0. Terminology — read this first

The Owner asked for a shift "from loops to the **gauntlet method** of graph engineering."

**Honest finding: "gauntlet" is not an established pattern in agent architecture.** The word
appears across several unrelated 2025–2026 projects that reused the name independently — a
Bittensor/Templar training-incentive pipeline, a multi-reviewer paper-comprehension system,
GauntletBench for web agents, and a P4 compiler fuzzer. A survey of the term concludes the shared
name "does not imply a shared mechanism"; it is a metaphor for *a deliberately adversarial
pipeline that evaluates contributions under reproducible rules*, not a formal pattern like
`saga` or `circuit breaker`.
(source: https://www.emergentmind.com/topics/gauntlet-pipeline)

So this plan does two things:

1. Keeps **"Gauntlet"** as the *internal product name* for Alfred's engine — it is a good name
   and it captures the intent exactly.
2. Uses **precise, industry-legible terms** for the mechanisms underneath, so the design can be
   reviewed against real prior art: **staged quality gates**, **progressive filtering**,
   **conditional edges**, **durable checkpointing**, **compensation**.

The Owner's instinct is sound and matches where the field is going. We just must not tell a
reviewer that "gauntlet" is a standard. It is ours.

**Definition for this repo:**

> **The Gauntlet** — an execution graph in which work advances only by passing explicit,
> observable **gates**. Every gate is a first-class node with a verdict, a cost, and a recorded
> reason. Failure routes to a *named* recovery edge, never to an implicit retry.

---

## 1. Why move off loops

Today's engine (`scripts/workflow.py`) is already a validated DAG runner: `waves()` for
parallelism, `topo_order()`, cycle rejection, `loop_to` with `backoff_delay()`, per-stage
timeouts, a per-run budget, `evaluate_when()` conditionals, and run history under `RUNS_DIR`.
That is genuinely more than most agent frameworks ship.

The limit is **how failure is handled**. `loop_to` re-runs a stage with feedback and a bounded
attempt count. That is a *retry loop wearing a DAG costume*. Four specific weaknesses:

| Weakness | Consequence |
|---|---|
| A retry has no *verdict object* — just "the trigger string was present" | We cannot tell "tests failed" from "the model refused" from "the tool timed out", so we always apply the same remedy |
| Feedback is a blob of prior text | The model can repeat a mistake it already made; nothing structurally forbids it |
| No durable mid-run checkpoint | A crashed run replays expensive LLM calls from the start |
| No compensation | A stage that half-applied a change leaves the tree dirty with no defined undo |

The field's answer is to make control flow **explicit and inspectable** rather than emergent.
LangGraph's model is instructive: a `StateGraph` of nodes and **conditional edges**, executed in
Pregel/BSP **supersteps**, with **checkpointers** that snapshot state at every superstep boundary
— enabling human-in-the-loop `interrupt()`/`Command(resume=)`, time-travel debugging, and
fault-tolerant resume without re-running completed work. It caps recursion (default 1000) and
exposes `RemainingSteps` so a graph can *degrade gracefully* before hitting the hard limit.
(sources: https://docs.langchain.com/oss/python/langgraph/graph-api ·
https://docs.langchain.com/oss/python/langgraph/pregel ·
https://docs.langchain.com/oss/python/langgraph/checkpointers)

And the mature workflow engines show what "reliable" actually costs. Temporal: per-Activity retry
policy with backoff coefficient and **schedule-to-close timeouts that bound total LLM spend**,
workflow-ID–keyed **idempotency**, **durable execution** via event-sourced replay, **first-class
compensation**, approval **gates via Signal**, and worker **versioning** to roll out new agent
logic without disrupting in-flight runs. Their framing is the one to steal: *"Retry is the
default… your agent code doesn't need to handle transient failure."*
(source: https://temporal.io/blog/from-agent-zoo-to-agent-orchestra-temporal-agentic-control-plane)

Notably, **Airflow and Prefect do not provide compensation/saga out of the box** — it must be
hand-coded. Temporal does. If we want undo semantics, we build them deliberately.

### The failure modes we are inheriting if we get this wrong

These are documented, not hypothetical:

| Failure mode | Evidence | Our mitigation |
|---|---|---|
| **Hidden cycles** silently burning budget | IBM found bad cycles in 57 / 1575 LangGraph trajectories; detection F1 only 0.72 (source: https://arxiv.org/html/2511.10650) | Structural cycle rejection at validate time (already done) **plus** a semantic no-progress detector (§3.4) |
| **State / memory explosion** | A missing `recursion_limit` spiking RAM 400MB → 2GB+ in a few hundred cycles (source: https://markaicode.com/errors/langgraph-memory-leak-fix/) | Hard step budget + checkpoint pruning + on-disk state, never in-memory only |
| **Checkpoint bloat** | In-memory savers fail on restart; Redis eviction silently drops state above ~500 sessions (source: https://markaicode.com/architecture/langgraph-memory-architecture/) | SQLite checkpoints with explicit TTL pruning |
| **Non-determinism** breaking replay | LLM output varies per call, so identical graphs produce different traces | Checkpoint-and-resume (replay *from* checkpoint), never "replay from scratch and expect the same thing" |
| **Cost blowups** | Unbounded retries / sub-agent spawning generating large bills | Budgets on stage-runs **and** estimated USD, checked *before* each call (Ultron already does this) |
| **Failure cascades** | "An agent's own past output is its future input" — errors compound within a run (source: https://dev.to/loopandretry/one-bad-step-n-bad-steps-how-agent-failures-cascade-538g) | A gate after every generative stage; independent parallel branches to contain blast radius |

---

## 2. Architecture — the Gauntlet Graph Engine

```
┌──────────────────────────────────────────────────────────────────────────┐
│ INTENT                    Owner objective → graph compiler                │
├──────────────────────────────────────────────────────────────────────────┤
│ GRAPH                     nodes · edges · gates · budgets                 │
│   work nodes    (an agent does a thing)                                   │
│   gate nodes    (a verdict: PASS / RETRY / ESCALATE / ABORT)              │
│   router edges  (conditional, verdict-driven — never implicit)            │
│   compensators  (named undo for a work node)                              │
├──────────────────────────────────────────────────────────────────────────┤
│ SCHEDULER                 waves · concurrency cap · superstep boundary    │
├──────────────────────────────────────────────────────────────────────────┤
│ DURABILITY                SQLite checkpoint per superstep · resume · TTL  │
├──────────────────────────────────────────────────────────────────────────┤
│ MEMORY GRAPH              bi-temporal facts · entities · episodes         │
├──────────────────────────────────────────────────────────────────────────┤
│ EXECUTORS                 alfred-coder (local) · deepseek · glm · opus    │
│                           · Notion MCP tools · deterministic tools        │
├──────────────────────────────────────────────────────────────────────────┤
│ POLICY                    the signed harness policy gates every side-effect│
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.1 The gate is the unit of progress

A gate node returns a **structured verdict**, never a string match:

```json
{
  "verdict": "RETRY",
  "confidence": 0.82,
  "reasons": [{ "code": "TESTS_FAILED", "detail": "3 failing in test_auth.py", "evidence": "exit=1" }],
  "remedy": "fix_tests",
  "costUsd": 0.004
}
```

Five verdicts, and each has exactly one legal routing:

| Verdict | Meaning | Routes to |
|---|---|---|
| `PASS` | acceptance criteria met, with evidence | the next wave |
| `RETRY` | recoverable, and we know the remedy | the **named remedy edge** (not "the same node again") |
| `REROUTE` | wrong approach entirely | an alternative subgraph — the anti-thrash rule, encoded |
| `ESCALATE` | needs a stronger model or the Owner | tier-up edge, or the Approvals List |
| `ABORT` | unsafe / budget exhausted / no path | compensation, then a partial result with a reason |

This is the **staged quality gate** pattern, and it directly encodes Alfred's existing
`escalation.md` anti-thrash rule: *two failures of the same approach → change the approach*.
Today that rule lives in prose and depends on a model choosing to obey it. `REROUTE` makes it
**structural** — after two `RETRY` verdicts with the same reason code, the router is *not
permitted* to emit `RETRY` again.

### 2.2 Attempt ledger — never repeat a known-failed approach

Every gate verdict appends to a per-run **attempt ledger** keyed by `(node, reason_code)`. The
ledger is injected into the next attempt's prompt as an explicit *"already tried, do not repeat"*
block. This is the documented "loop engineering" idea — each pass records what it tried so it
never repeats the same mistake — but enforced by the engine rather than requested of the model.
(source: https://yanxbt.substack.com/p/loop-engineering-the-karpathy-method)

### 2.3 Durable checkpointing

One SQLite row per superstep boundary: `run_id`, `superstep`, `node_states`, `ledger`, `budget_spent`.
Resume replays *from* the checkpoint, so completed LLM calls are never re-billed. TTL pruning
keeps the store bounded. This is the single highest-value borrow from LangGraph and Temporal, and
it is what turns a crash from "lost work" into "resume."

### 2.4 Semantic no-progress detection

Structural cycle detection is already in place and is not enough — the documented failure is a
*semantically* looping graph that never errors. Cheap, deterministic detector, no LLM needed:
hash the normalized artifact each stage produces; if a node produces a hash it has produced
before, emit `REROUTE`, not `RETRY`. Two identical outputs is definitionally no progress.

### 2.5 Compensation

Each mutating work node may declare `compensate: <capability>`. On `ABORT`, the engine runs the
compensators of completed mutating nodes in reverse order. Every compensator must be an existing
**harness capability**, so undo is policy-gated too — the engine cannot invent a destructive
action. Airflow and Prefect leave this to you; we build it explicitly because Alfred's whole
premise is that a failure must leave the machine in a consistent state.

---

## 3. The Memory Graph

Today Alfred has `memory.jsonl` + a SQLite FTS5 "megamind" + local embeddings. That is good recall
of *episodes*. What it cannot do is answer **"what is true now, and what changed?"** — because
appending never retracts.

### 3.1 Bi-temporal facts (the key idea)

Graphiti/Zep model two independent timelines: **T** — when a fact was true in the world
(`t_valid`, `t_invalid`) — and **T′** — when the system learned it (`t_created`, `t_expired`).
When a new fact contradicts an old one, the old edge's `t_invalid` is set to the new fact's
`t_valid`. **History is preserved, not deleted.**
(source: https://arxiv.org/html/2501.13956)

This matters concretely for Alfred: "the Owner prefers X" is a fact that *changes*. A flat log
gives contradictory recall forever. A bi-temporal graph answers "current preference" *and*
"when did it change, and why."

Published results for the approach: **LongMemEval 71.2% with gpt-4o vs 60.2% full-context
(+18.5%), with ~90% latency reduction** (2.58s vs 28.9s) by retrieving ~1.6k tokens instead of
115k. DMR 94.8% vs MemGPT's 93.4%. (source: https://arxiv.org/html/2501.13956)

The latency/token figure is the one that matters for us — it is the same principle as
`token-economy`: retrieve a bounded, relevant slice instead of stuffing the window.

### 3.2 Schema

Four node types and typed edges — deliberately smaller than Graphiti's full ontology:

| Node | Holds | Notes |
|---|---|---|
| `episode` | raw session/tool output | provenance ground truth; never rewritten |
| `entity` | project, file, agent, model, person, concept | carries an evolving summary + embedding |
| `fact` | a claim with `t_valid` / `t_invalid` | the bi-temporal core |
| `community` | cluster summary | built lazily; only when the graph is large enough to need it |

Edges carry `relation_type`, `confidence`, `source_episode`, and the four timestamps. Every fact
is traceable to the episode that produced it — non-negotiable, because Alfred's identity rule is
*never fabricate*. A fact with no episode is a bug.

Also carried, because Alfred specifically needs them: `decision` and `preference` fact subtypes
(mapping to today's `memory/decisions.md` and Owner preferences), and `outcome` linked to the
`run_id` that produced it — so the memory graph and the execution graph are joined.

### 3.3 Local implementation — no server, no cloud

Constraint: Windows, no service to run, no cloud dependency, must work offline.

| Option | Verdict |
|---|---|
| **SQLite + recursive CTEs + FTS5 + `sqlite-vec`** | **Chosen.** `sqlite-vec` is pure C, zero-dependency, MIT/Apache-2.0, stable since 2024, runs anywhere SQLite runs. Extends the megamind DB we already have. (source: https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html) |
| Kuzu (embedded property graph) | **Rejected — archived Oct 2025, unmaintained; Graphiti deprecated its Kuzu driver.** (source: https://github.com/kuzudb/kuzu) |
| DuckDB + DuckPGQ | Credible alternative for analytical graph queries, but PGQ is a *community* extension, not documented as stable. Revisit later. |
| NetworkX | In-memory only, no persistence. Useful for graph *algorithms* on a loaded subgraph, not as the store. |
| Neo4j / hosted graph DB | Violates the no-server, offline-first constraint. |
| Engrava (SQLite agent-memory lib, MIT) | Worth evaluating — it wraps exactly this stack (FTS5 + `sqlite-vec`, bi-temporal, consolidation, hygiene). Read it for design ideas; adopting it adds a dependency, which is a real cost given Ultron's zero-dep rule. |

Migration is additive: new tables beside `megamind.db`, backfilled from `memory.jsonl`. Nothing
existing is deleted. `alfred-recall.ps1` keeps working; a graph-aware path is added alongside.

### 3.4 Retrieval

Hybrid, mirroring Zep: **BM25 (FTS5) + vector (`sqlite-vec`) + bounded graph traversal** seeded
from recent episodes, fused with reciprocal-rank fusion, then reranked (episode-mention frequency
and node distance are cheap and need no model; cross-encoder is the expensive option and stays
off by default). (source: https://arxiv.org/html/2501.13956)

Hard rule: **retrieval returns a token-bounded context block**, never "everything relevant." The
budget is a parameter, and exceeding it truncates by rank. This is `token-budget.md` applied to
memory.

### 3.5 Hygiene

- **Dedup:** embed → cosine candidates → merge on high similarity. LLM adjudication only for
  genuinely ambiguous pairs, so the common path stays free.
- **Contradiction:** classify new vs existing as compatible / contradictory / subsumes. Contradictory
  → invalidate the old edge with `t_invalid`; never hard-delete.
- **Decay:** relevance-modulated exponential decay by recency and access frequency. FadeMem reports
  82.1% critical-fact retention at 55% storage on LTI-Bench with this approach.
  (source: https://arxiv.org/html/2601.18642v1)
- **Consolidation:** cluster related facts and emit summary/reflection nodes. Do this
  deterministically via embedding centroids first (no LLM); add LLM summarization only where it
  demonstrably helps.

---

## 4. Alfred + Ultron — one graph, two runtimes

They are not competitors and should not be merged. They have different jobs:

| | Alfred harness | Ultron CLI |
|---|---|---|
| Owns | policy, safety, memory, the machine | model access, the REPL, portable pipelines |
| Language | Python | Node, zero deps |
| Trust | signed policy, per-caller allowlists | permission profiles |
| Runs | this machine, gated | anywhere |

**The contract between them is a shared graph spec.** Alfred's `workflows/*.json` and Ultron's
`.ultron/pipelines/*.json` converge on one schema (`gauntlet/v1`) that both engines validate and
execute. Then:

- Alfred **compiles and owns** the graph, holds the memory, and enforces policy.
- Ultron **executes model-facing nodes** — it already has the provider registry, the tier ladder,
  budget enforcement before each call, and hook veto.
- Alfred invokes Ultron as a **harness capability**, so every model call inherits the signed
  policy and the audit trail.

Concretely: a new `ultron-pipeline` capability in `policy/harness-policy.json`, `gated: true`
(it spends money and spawns agents), callable by `owner` and `kiro-agent` but **never** by
`local-model`.

### 4.1 Model routing across the graph

Per-node tier, resolved by Ultron's existing ladder:

| Node kind | Model | Why |
|---|---|---|
| gate / verdict / classify | `alfred-coder-7b` local, **$0** | short structured output; the highest-frequency node type |
| bulk implementation, summarize, extract | `deepseek-v4-flash` (~$0.14/1M in) | cheapest capable API tier |
| reasoning-heavy implementation | `glm-5.2` | thinking mode |
| hard engineering, security, architecture | `deepseek-v4-pro` | cheap for its class |
| orchestration, graph compilation, genuinely hard calls | **Opus 5** | protect this tier |
| long-horizon / 1M-context document work | `kimi-k3` | when context truly demands it |

Because gates are the most numerous nodes and run locally for free, **adding more gates should
make the system cheaper and more reliable at once.** That is the central economic bet of this
plan, and it is falsifiable — §6 measures it.

### 4.2 Notion MCP as a graph surface

Verified constraint, and it must not be misdesigned around: **Notion AI is not an inference
endpoint.** No documented API sends an arbitrary prompt to Notion AI; the Agent APIs are workspace
data and tool access. Notion is therefore a **tool/data node**, never a model node.
(sources: https://developers.notion.com/guides/mcp/overview ·
https://www.notion.com/blog/introducing-developer-platform)

What it *does* give us, and it is genuinely useful:

- **Graph input** — `notion-search` / `notion-fetch` as source nodes for a research subgraph.
- **Graph output** — write run reports, decision records, and Approvals Lists into Notion as
  first-class artifacts the Owner can read on a phone.
- **Human gates** — a `notion-create-pages` node posts an approval request; the graph parks at an
  `interrupt` and resumes on a checkbox. This is Temporal's Signal-based approval gate, using
  Notion as the UI.

Auth is already built: OAuth 2.1 + PKCE S256 + RFC 7591 dynamic client registration, tokens
AES-256-GCM encrypted with atomic refresh-token rotation. Caveat to keep in mind: AI-backed MCP
tools require a Business/Enterprise plan with Notion AI, so the graph must read the runtime
`current_tool_access` map and route around unavailable tools instead of failing.
(source: https://developers.notion.com/guides/mcp/mcp-supported-tools)

---

## 5. Delivery plan

Each phase is independently shippable, leaves every suite green, and is additive — the current
loop engine keeps working until the graph engine provably beats it.

| Phase | Scope | Done when |
|---|---|---|
| **1. Verdict protocol** | `gauntlet/v1` schema; `Verdict` type; gate-node contract; spec validator accepting both old `loop_to` and new gates | Round-trip tests; every existing `workflows/*.json` still validates |
| **2. Gate routing + attempt ledger** | Verdict-driven router; ledger keyed by `(node, reason_code)`; **`RETRY` forbidden twice on the same reason → forced `REROUTE`** | Test: a stage failing identically twice reroutes instead of retrying a third time |
| **3. Durable checkpoints** | SQLite checkpoint per superstep; `--resume <run_id>`; TTL pruning | Test: kill a run mid-wave, resume, assert completed nodes are **not** re-executed |
| **4. No-progress detector** | Artifact-hash comparison per node → `REROUTE` on repeat | Test: a node returning identical output twice is rerouted, not retried |
| **5. Compensation** | `compensate:` on mutating nodes; reverse-order rollback on `ABORT`; compensators must be harness capabilities | Test: a failed mutating run leaves the tree clean; a non-capability compensator is rejected at validate time |
| **6. Memory graph core** | New SQLite tables; `sqlite-vec`; bi-temporal facts; backfill from `memory.jsonl`; `graph-recall` capability | Test: contradicting a fact invalidates the old edge and keeps history; recall returns the current fact |
| **7. Hybrid retrieval** | BM25 + vector + bounded traversal, RRF, cheap rerankers, hard token budget | Test: retrieval never exceeds the token budget; ranking beats FTS-only on a fixture set |
| **8. Ultron bridge** | `gauntlet/v1` in Ultron; `ultron-pipeline` harness capability (gated) | Test: one spec file runs on both engines with equivalent stage outcomes |
| **9. Notion graph I/O** | Source/sink nodes; human-approval gate via `interrupt` + resume | Test with a stub MCP server; live test needs the Owner's account |
| **10. Hygiene + consolidation** | Dedup, contradiction, decay, deterministic consolidation | Test: dedup merges, decay preserves high-value facts under a storage cap |

**Sequencing note:** phases 1–5 are the execution graph, 6–7 the memory graph, 8–10 the
integration. 1–5 deliver value without any of 6–10, so if the plan stalls, it stalls having
already improved reliability.

---

## 6. How we will know it worked

Falsifiable, measured before and after. No vibes.

| Metric | Baseline | Target |
|---|---|---|
| Repeated-identical-failure rate | measure on current `loop_to` runs | → 0 (structurally impossible after phase 2) |
| Median USD per completed objective | measure | ↓, because gates run locally for free |
| Wasted spend on aborted runs | measure | ↓ via pre-call budget checks + early `ABORT` |
| Re-billed LLM calls after a crash | 100% today (no resume) | → ~0 after phase 3 |
| Recall tokens per query | current full-context recall | bounded, target ≥50% reduction |
| Contradictory recall incidents | count on the current flat log | → 0 for facts with bi-temporal coverage |
| Runs left in a dirty state after failure | measure | → 0 after phase 5 |

If gates do **not** reduce cost, the central bet in §4.1 is wrong and we say so rather than
shipping a more expensive system with a nicer diagram.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| **Over-engineering.** A graph engine is a big build; the current one already works | Phases are additive and independently valuable; the loop engine is never removed until the graph engine beats it on §6 |
| **Gate latency.** More gates = more calls | Gates run on the free local model and return short structured output; measure §6 |
| **Local gate quality.** A 7B model judging a verdict may be unreliable | Gates are *classification with a fixed schema*, the easiest task class. Where a gate is provably weak, tier it up on that node only. Validate against a labelled fixture set before trusting it |
| **Checkpoint bloat** | TTL pruning from day one, not bolted on (documented failure mode, §1) |
| **Memory graph rot** | Provenance is mandatory; a fact with no source episode is a bug, not a warning |
| **`sqlite-vec` immaturity** | Stable since 2024, MIT/Apache-2.0, pure C, zero deps — but pin the version and keep the FTS5 keyword path as a working fallback |
| **CPU-only inference here** | This machine has no dedicated GPU; 7B gate calls are fast but long generations are not. Keep generation on API tiers, gates local |
| **Scope creep into a framework** | We are not building LangGraph. We are building the smallest graph engine that enforces Alfred's own safety and honesty rules |

---

## 8. Open questions for the Owner

1. **Gate model.** Start all gates on local `alfred-coder-7b` ($0, may need tuning), or start on
   `deepseek-v4-flash` (~$0.14/1M, more reliable) and move them local once measured? *Recommendation:
   start on flash, measure, then migrate down — correctness before savings.*
2. **Adopt Engrava** (MIT, wraps exactly our chosen stack) or build the memory graph in-repo?
   *Recommendation: read it for design, build in-repo — Alfred's value is that we own and understand it.*
3. **Notion as the approval UI** for human gates, or keep the Approvals List in `memory/todo.md`?
   Notion is nicer on a phone; `todo.md` works offline.
4. **Opus 5 budget.** What monthly cap should the engine enforce for the top tier before it
   refuses to escalate?

---

## Appendix — sources

Execution graphs: LangGraph graph API, Pregel model, checkpointers
(https://docs.langchain.com/oss/python/langgraph/graph-api ·
https://docs.langchain.com/oss/python/langgraph/pregel ·
https://docs.langchain.com/oss/python/langgraph/checkpointers) ·
Temporal agentic control plane
(https://temporal.io/blog/from-agent-zoo-to-agent-orchestra-temporal-agentic-control-plane) ·
bad-cycle detection in agent trajectories (https://arxiv.org/html/2511.10650) ·
failure cascades (https://dev.to/loopandretry/one-bad-step-n-bad-steps-how-agent-failures-cascade-538g) ·
quality gates (https://dev.to/yurukusa/why-ai-agent-needs-a-quality-gate-not-just-tests-42eo) ·
loop engineering (https://yanxbt.substack.com/p/loop-engineering-the-karpathy-method) ·
"gauntlet" disambiguation (https://www.emergentmind.com/topics/gauntlet-pipeline)

Memory graphs: GraphRAG (https://arxiv.org/html/2404.16130v2) ·
LazyGraphRAG (https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) ·
Zep/Graphiti bi-temporal model + benchmarks (https://arxiv.org/html/2501.13956) ·
Graphiti implementation (https://github.com/getzep/graphiti) ·
FadeMem decay/consolidation (https://arxiv.org/html/2601.18642v1) ·
sqlite-vec stable release (https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html) ·
Kuzu archived (https://github.com/kuzudb/kuzu) ·
DuckPGQ (https://www.duckdb.org/2025/10/22/duckdb-graph-queries-duckpgq) ·
Engrava (https://pypi.org/project/engrava/)

Notion: MCP overview + supported tools (https://developers.notion.com/guides/mcp/overview ·
https://developers.notion.com/guides/mcp/mcp-supported-tools)

**Unverified / flagged:** Argo Workflows specifics were not confirmed from a primary source and
are omitted. Prefect mechanisms come from search snippets, not a fetched page. Engrava has no
published LongMemEval scores that I could find. "Gauntlet" is **not** an industry-standard
pattern — it is our internal name.
