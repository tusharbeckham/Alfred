# 🎩 Alfred

A personal, self-improving, multi-agent AI system built on **Kiro Pro Max**.
Alfred codes, manages your PC, and runs your projects — autonomously where safe,
with your approval where it matters.

## What Alfred is

Alfred is an **orchestration layer** over Kiro's Claude models (Opus 4.8 / 4.6,
Sonnet 4.6). 19 specialized agents across 4 tiers collaborate through DAG pipelines,
each with its own **brain** (identity, reasoning, instincts, knowledge, memory, reflexes)
and a shared **training system** that improves prompts and skills over time.

> Alfred does **not** train model weights. "Training" = eval-driven prompt & skill
> optimization. See `AGENTS.md`.

## Quick Start

```powershell
# From the project root
cd C:\Alfred

# Talk to your assistant / production manager
kiro-cli chat --agent alfred-manager

# Or let the orchestrator run a task end-to-end
kiro-cli chat --agent alfred-leader "Build a Python CLI word-counter with tests"
```

Inside a session:
- `Ctrl+Shift+A` — jump to `alfred-manager`
- `/agent` — list/switch agents
- `/context show` — see loaded steering, skills, memory
- `Ctrl+G` — monitor spawned subagents

## Layout

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Top-level governance (mission, org chart, safety) |
| `.kiro/agents/` | 19 agent configs |
| `.kiro/brains/` | Per-agent cognition (identity, memory, skills, reflexes) |
| `.kiro/steering/` | Always-on rules (identity, conventions, safety, reporting, escalation) |
| `.kiro/skills/` | On-demand domain expertise (11 skills) |
| `.kiro/settings/` | CLI + MCP settings |
| `prompts/` | Versioned prompt library |
| `hooks/` | Lifecycle reflex scripts (PowerShell) |
| `scripts/` | Automation: overnight, morning report, CI, training |
| `evals/` | Eval datasets + rubrics + results |
| `training/` | Prompt versions, A/B logs, improvement history |
| `memory/` | Persistent decisions, learnings, todo, logs |
| `mcp/` | Custom Alfred MCP server |

## Automation

| Script | What it does |
|--------|--------------|
| `scripts/overnight-run.ps1` | Works the `memory/todo.md` backlog overnight (sandboxed, CI-gated) |
| `scripts/morning-report.ps1` | Briefs you at dawn from `memory/` |
| `scripts/ci-run.ps1` | Runs tests/lint/build; gates commits |
| `scripts/train.ps1` | Runs the eval-driven self-improvement loop |
| `scripts/run-eval-loop.ps1` | Runs evals on demand |
| `scripts/spawn-agent.ps1` | Scaffolds + validates a new agent |

See `implementation-plan.md` for the full design and build sequence.
