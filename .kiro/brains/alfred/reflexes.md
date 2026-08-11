# Reflexes — alfred (supreme overseer)

Automatic lifecycle reactions wired in `.kiro/agents/alfred.json` → `hooks`.

| Hook | Matcher | Script | Purpose |
|---|---|---|---|
| `agentSpawn` | — | `hooks/on-spawn.ps1` | Log session start (who, when) to `memory/session-log.txt`. |
| `preToolUse` | `write` | `hooks/pre-write.ps1` | Guard before any file write: block secret-bearing paths, log the target. |
| `postToolUse` | `shell` | `hooks/post-shell.ps1` | Audit every shell command Alfred runs to `memory/shell-log.txt`. |
| `stop` | — | `hooks/on-stop.ps1` | Append a session summary (decisions, outcomes) to `memory/decisions.md`. |

## Why Alfred carries write/shell reflexes at all
Alfred delegates most file editing and shell execution — but he does write plans, memory
entries, and orchestration state himself, and he runs read-only inspection commands to verify
subagent claims. Those actions are logged so the trail is complete even when Alfred acts directly.

## Reflex-adjacent guarantees
- **Write** is auto-approved but path-constrained: allowed under `C:/Alfred/**` and
  `C:/projects/**`; denied on Windows/Program Files, `.env*`, `secrets/**`, key material,
  `.kiro/settings/**`, and the signed harness policy files.
- **Shell** auto-approves read-only commands only; every destructive/system pattern is denied
  outright by regex in `toolsSettings.shell.deniedCommands`.
- **Subagent** is auto-approved for the entire agent registry — deliberate, so orchestration
  never stalls on a permission prompt.

## Manual reflexes Alfred is expected to perform
- Recall before planning: `python scripts/megamind.py recall -q "<objective>" -k 5`.
- Capture after a meaningful session:
  `powershell -NoProfile -File scripts/alfred-capture.ps1 "decision|<topic>|<what + why>|tags"`.
- Checkpoint the plan into `todo_list` before any pipeline with more than two stages.
