# Alfred Brain System

Every agent is a *thinking entity*, not a stateless prompt. A brain has 6 layers:

1. **Identity Core** — `.kiro/brains/<agent>/identity.txt` (the agent's `prompt`).
2. **Reasoning Engine** — effort level. Ultrathink (`effort: max`) for manager, leader,
   architect (opus 4.8/4.6). Others use their model default and can raise with `/effort`.
3. **Instincts** — the `.kiro/steering/` files, inherited by every agent automatically.
4. **Knowledge** — skills in `.kiro/skills/` (metadata always loaded; body on demand).
5. **Memory** — the shared `memory/` knowledge base (semantic) + per-brain `memory/` notes.
6. **Reflexes** — hooks in `hooks/` wired into each agent's config.

`alfred-manager` and `alfred-leader` have full reference brains (`brain.md`, `skills.md`,
`reflexes.md`, `memory/`). Every other agent has its own `identity.txt` and shares the
framework; its cognition profile is tabulated below.

## Per-agent cognition profile

| Agent | Reasoning | Primary skills | Key reflexes |
|-------|-----------|----------------|--------------|
| alfred-manager | ultrathink | orchestration, self-improvement | spawn, stop |
| alfred-leader | ultrathink | orchestration, git-workflows, ci-cd | spawn, stop |
| alfred-architect | ultrathink | architecture, security | spawn, stop |
| alfred-planner | high | orchestration, architecture | spawn |
| alfred-prompt-engineer | high | prompt-engineering, self-improvement | spawn |
| alfred-coder | high | coding, debugging | pre-write, post-shell, stop |
| alfred-tester | high | coding, ci-cd | post-shell, stop |
| alfred-reviewer | high | coding, security | spawn |
| alfred-researcher | medium | (web research) | spawn |
| alfred-debugger | high | debugging, coding | post-shell, stop |
| alfred-devops | high | ci-cd, git-workflows | post-shell, ci-gate, stop |
| alfred-pc-ops | medium | pc-management | pre-write, post-shell |
| alfred-security | high | security, coding | spawn |
| alfred-docs | medium | (documentation) | stop |
| alfred-data | high | (data analysis) | post-shell |
| alfred-evaluator | high | self-improvement | stop |
| alfred-trainer | ultrathink | self-improvement, prompt-engineering | stop |
| alfred-memory-curator | medium | (knowledge upkeep) | stop |
| alfred-agent-builder | high | mcp-building, prompt-engineering | stop |
| alfred-math | high | mathematics, coding | pre-write, post-shell, stop |
| alfred-physics | high | physics, coding | pre-write, post-shell, stop |
| alfred-backend | high | coding, security | pre-write, post-shell, stop |
| alfred-ml | high | coding, mathematics, self-improvement | pre-write, post-shell, stop |
| alfred-cloud | high | cloud-native, architecture, security | pre-write, post-shell, stop |
| alfred-sre | high | reliability, debugging, ci-cd | post-shell, stop |
| alfred-frontend | high | frontend, coding, security | pre-write, post-shell, stop |
| alfred-release | high | ci-cd, git-workflows | post-shell, stop |
| alfred-perf | high | performance, debugging, coding | post-shell, stop |
| alfred-data-engineer | high | data-engineering, coding, cloud-native | pre-write, post-shell, stop |
| alfred-qa | high | quality-assurance, coding, debugging | post-shell, stop |
| alfred-integrations | high | api-integration, coding, security | pre-write, post-shell, stop |
| alfred-product | medium | product, architecture | stop |
| local-coder (opt-in) | sonnet dispatcher → local qwen2.5-coder | coding (routine/low-stakes only) | pre-write, post-shell, stop |

All agents inherit steering (safety/escalation/reporting) and the shared memory KB.
