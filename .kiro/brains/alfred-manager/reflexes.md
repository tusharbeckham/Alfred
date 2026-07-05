# Reflexes — alfred-manager

Hooks that fire automatically for this agent (Brain Layer 6). Definitions in `hooks/`.

- **agentSpawn** → `hooks/on-spawn.ps1` — log session start (agent, timestamp, session id).
- **stop** → `hooks/on-stop.ps1` — append a session summary to `memory/decisions.md`.

The manager does not use write/CI reflexes — it delegates execution, so it has no
pre-write or post-shell reflexes of its own.
