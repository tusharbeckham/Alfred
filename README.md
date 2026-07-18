# Alfred

> A personal, self-improving multi-agent AI system — with a **bespoke local coder you fine-tune and own**, persistent offline memory, live web access, and an **offline voice**.

![Kiro](https://img.shields.io/badge/Built%20on-Kiro-000000?style=flat-square)
![Claude](https://img.shields.io/badge/Claude-Opus%20%2F%20Sonnet-D97757?style=flat-square)
![Local LLM](https://img.shields.io/badge/Local-Qwen2.5--Coder%207B-615CED?style=flat-square)
![QLoRA](https://img.shields.io/badge/Fine--tune-QLoRA%20(free)-EE4C2C?style=flat-square)
![PowerShell](https://img.shields.io/badge/PowerShell-5391FE?style=flat-square&logo=powershell&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Voice](https://img.shields.io/badge/Voice-Piper%20TTS%20(offline)-8A2BE2?style=flat-square)

Alfred is a personal AI operating layer: a coordinated team of specialized agents that **code, manage a Windows PC, and run projects** — backed by a locally fine-tuned model, persistent memory, and web access. It runs on frontier models when it matters and a **free, offline local model** for everything routine.

---

## Highlights

- **Multi-agent orchestration** — an overseer plus 4 tiers of specialized agents (manager, leader, architect, coder, tester, reviewer, researcher, debugger, devops, security, docs, data, ML, backend, cloud, SRE, frontend, and more) that collaborate through DAG pipelines with loops and fan-in.
- **Executable workflow engine** — declarative DAG workflows (`workflows/*.json`) run by a validated, tested scheduler (`scripts/workflow.py`): automatic parallel waves, fan-in, bounded loops with **backoff+jitter**, per-stage **timeouts**, per-run **budgets**, **conditional** stages, and **run history** — with plan/graph/dry-run previews before anything spawns.
- **Bespoke local coder (offline, $0)** — a **Qwen2.5-Coder-7B fine-tuned via QLoRA** on a free cloud GPU, served locally through LM Studio's OpenAI-compatible API. It learns the system's own voice, routing, and safety rules.
- **Hybrid routing** — routine, low-stakes work runs on the free local model; complex, architectural, or sensitive work escalates to frontier models. Correctness over credit-savings.
- **Persistent "megamind" memory** — structured episodic memory in a **local SQLite database (FTS5, sub-millisecond recall)** plus **offline semantic recall** via a local embedding model, so the assistant remembers decisions and preferences **with or without the cloud**.
- **Live web access** — keyless search + page-fetch available to every agent (and to the local model when online).
- **Offline voice** — Alfred *speaks*: a local **neural text-to-speech voice (Piper)** with a built-in Windows fallback. Ask a question and hear the answer — `ask`, `talk`, and `say`, all offline, no keys.
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
| `.kiro/steering/` | Always-on rules (identity, safety, routing, reporting, memory, web) |
| `.kiro/skills/` | On-demand domain expertise |
| `scripts/` | Automation: **workflow engine**, **security tools**, **productivity tools**, local coder, memory, web, voice (TTS), fine-tune builder, CI, training |
| `workflows/` | Declarative multi-agent DAG workflow specs (run by `scripts/workflow.py`) |
| `evals/` | Eval datasets + rubrics |
| `docs/` | Setup and workflow guides |
| `notebooks/` | Fine-tune notebook |

> Personal data — the memory trail, fine-tune datasets, eval outputs, and secrets — is kept **local-only** and git-ignored by design.

---

## Tech

Kiro · Claude (Opus / Sonnet) · LM Studio · Qwen2.5-Coder · Unsloth · QLoRA · local embeddings (RAG-style memory) · Piper TTS · MCP · PowerShell · Python.
