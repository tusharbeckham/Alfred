# Reflexes — alfred-leader

- **agentSpawn** → `hooks/on-spawn.ps1` — log orchestration session start.
- **stop** → `hooks/on-stop.ps1` — append a pipeline summary (stages, outcomes) to
  `memory/decisions.md`.

The leader delegates file edits and shell to workers, so it carries no pre-write /
post-shell reflexes itself.
