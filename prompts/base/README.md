# Prompts Library

The versioned catalog of **execution & workflow prompts** — the reusable instructions the
Owner and the agents run. This is distinct from agent *personas*.

## Two kinds of prompts
1. **Agent personas (system prompts)** live in `.kiro/brains/<agent>/identity.txt`. They
   are Brain Layer 1. Each agent's config `prompt` field references its own `identity.txt`.
2. **Execution/workflow prompts** live here in `prompts/`. They are task instructions you
   invoke (e.g., "run the overnight backlog", "optimize this prompt", "review this diff").

## The Prompt Service
`alfred-prompt-engineer` owns both kinds. It:
- Generates and optimizes agent personas (edits `identity.txt` files).
- Maintains these execution prompts and versions improvements under `training/`.
When any agent needs a better prompt for a stage, the leader asks the prompt-engineer.

## Categories
- `base/` — shared preamble (`common.txt`) + this catalog.
- `coding/` — code review, test-fixing, refactoring task prompts.
- `self-improvement/` — prompt optimization & self-critique.
- `overnight/` — unsupervised run + morning report (used by `scripts/`).
- `ci-cd/` — CI run + deploy verification.
- `training/` — the eval-driven training loop (used by `scripts/train.ps1`).
- `orchestration/` — runnable workflow templates for the leader.

## Convention
Keep each prompt focused and composable. Start task prompts by referencing `base/common.txt`
so every run inherits Alfred's shared operating rules.
