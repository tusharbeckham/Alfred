# Alfred MCP Servers

Config: `.kiro/settings/mcp.json`. Inspect with `kiro-cli mcp list`; check status in a
session with `/mcp`.

## Enabled

### `alfred` (custom, dependency-free Node)
`mcp/alfred-server.js` — Alfred's own tools:
- `read_memory {file}` / `write_memory {file, content, mode}` — access `memory/`.
- `query_eval_results` — latest eval results from `evals/results/`.
- `list_agents` — the roster with models + descriptions.
- `trigger_train {confirm}` / `trigger_overnight {confirm}` — return the launch command;
  only actually launch when `confirm=true` (safety-gated).

Requires Node (verified present). No install needed.

## Staged (disabled — enable per your environment)

Set `"disabled": false` on a server once its prerequisites are met, then restart the session.

| Server | Runtime | Prerequisite |
|--------|---------|--------------|
| `filesystem` | `npx` (Node ✓) | first run downloads `@modelcontextprotocol/server-filesystem` |
| `memory` | `npx` (Node ✓) | first run downloads `@modelcontextprotocol/server-memory` |
| `github` | `npx` (Node ✓) | set `$GITHUB_TOKEN` env var (repo scope) |
| `git` | `uvx` | install `uv` (currently MISSING): `pip install uv` |
| `fetch` | `uvx` | install `uv` |
| `time` | `uvx` | install `uv` |
| `sqlite` | `uvx` | install `uv` |

> Why staged? A server whose runtime/binary is missing prints errors on every launch.
> Staging them disabled keeps sessions clean until you opt in. `uv`/`uvx` are not installed
> on this machine; the git/fetch/time/sqlite servers need it.

## Exposing MCP tools to an agent
MCP tools appear as `@server/tool`. Auto-approve read-only ones per agent, e.g.:
```json
"allowedTools": ["read", "grep", "@alfred/read_memory", "@alfred/list_agents"]
```
The custom `alfred` server is safe to trust (local, no network, path-guarded).
