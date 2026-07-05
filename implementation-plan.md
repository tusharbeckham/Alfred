# Implementation Plan — Alfred (Enhanced Multi-Agent AI System)

## What Alfred is
An orchestration system on Kiro Pro Max (Claude Opus 4.8/4.6, Sonnet 4.6). NOT a
from-scratch LLM. "Training" = eval-driven prompt & skill optimization. No Kaggle, no
model-weight training.

## Environment
- Windows, project root `C:\Alfred`.
- Edit with VS Codium; run with Kiro CLI (`kiro-cli` 2.11.0).
- Hooks/automation are PowerShell (`.ps1`).

## Model Tiers (strict)
- `alfred-manager`, `alfred-leader`: `claude-opus-4.8`
- Workers + meta-agents: `claude-opus-4.6` (never below 4.6)
- `alfred-planner`, `alfred-prompt-engineer`: `claude-sonnet-4.6`
- If a model ID is unavailable in-region, Kiro falls back to the default model.

## Workflow decisions
- Agent-guided automation: `alfred-leader` builds/guides DAG pipelines.
- `alfred-prompt-engineer` provides a prompt service to all agents.
- Loops owned by specific agents: `alfred-trainer` (improvement/eval), `alfred-leader` (orchestration/retry).
- "Smartest team": agents may pick better tools/approaches during research.
- Hybrid supervision: autonomous for routine; owner confirms serious/destructive/system/prod actions.

## Brain System (per agent, `.kiro/brains/<agent>/`)
6 layers: Identity Core (identity.txt) · Reasoning Engine (effort/ultrathink) · Instincts
(steering) · Knowledge (skills) · Memory (knowledge base + memory/) · Reflexes (hooks).
Folder: `brain.md`, `identity.txt`, `memory/`, `skills.md`, `reflexes.md`.

## Training System (eval-driven)
Evaluate → Score → Diagnose → Optimize → Regression-test → Accumulate → repeat.
`evals/` datasets+rubrics+results; `training/` versioned prompts + history. Owned by
`alfred-trainer`, run by `alfred-evaluator`, rewrites by `alfred-prompt-engineer`.
Nightly + on-demand via `scripts/train.ps1`. Regression suite guards against degradation.

## Team Roster (19)
Tier 0: alfred-manager. Tier 1: alfred-leader, alfred-architect, alfred-planner,
alfred-prompt-engineer. Tier 2: alfred-coder, -tester, -reviewer, -researcher, -debugger,
-devops, -pc-ops, -security, -docs, -data. Tier 3: alfred-evaluator, -trainer,
-memory-curator, -agent-builder.

## Task Breakdown
1. Scaffold + Governance Core (AGENTS.md, README.md, cli.json).
2. Steering (identity, conventions, safety, reporting, escalation).
3. Brain System Framework (manager + leader reference brains).
4. Prompts Library (base, coding, self-improvement, overnight, ci-cd, training, orchestration).
5. alfred-manager (opus 4.8, Production Manager).
6. alfred-leader (opus 4.8, orchestrator + ultrathink).
7. Skills Library (11 SKILL.md).
8. Worker Team (10 workers, opus 4.6+).
9. Meta-Agents + remaining leadership.
10. Hooks (on-spawn, on-stop, pre-write, post-shell, ci-gate).
11. MCP Servers (git, github, filesystem, fetch, time, sqlite, memory + custom Alfred MCP).
12. Knowledge Bases (per-agent long-term memory).
13. Training System (evals, rubrics, store, train loop).
14. Dynamic Workflows & Agent Loops.
15. Overnight Runs + Morning Report + CI/CD.
16. Multi-Repo Orchestration (git worktrees).
17. End-to-End Integration + Ultracode validation.

## Verification
Validate every agent JSON with `kiro-cli agent validate --path <file>`. Confirm context
loads via `/context show`. Test hooks by triggering lifecycle events. Log decisions to
`memory/decisions.md`.
