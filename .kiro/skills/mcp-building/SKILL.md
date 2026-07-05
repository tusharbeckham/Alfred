---
name: mcp-building
description: Building and configuring MCP servers and new agents — custom tools, server config, and agent scaffolding/validation. Use when extending Alfred's tools or creating new agents.
---

# MCP Building & Agent Tooling

## MCP servers in Kiro
Configure in an agent's `mcpServers` or in `.kiro/settings/mcp.json`.

**Local (stdio) server:**
```json
{ "mcpServers": { "git": { "command": "mcp-server-git", "args": ["--stdio"], "timeout": 120000 } } }
```
**Remote (HTTP) server:** use `url` + optional `headers`/`oauth`.
- `env` for secrets via `$VAR` expansion. `disabled: true` to stage a server that isn't
  installed yet. `disabledTools` to hide specific tools.
- Scope exposure per agent with `allowedTools` (`@server/tool` or `@server/*`).

## Building a custom MCP server
- Speak MCP over stdio (JSON-RPC). Expose a small set of well-named tools.
- Each tool: valid name (`^[a-zA-Z][a-zA-Z0-9_]*$`, ≤64 chars incl. server prefix), a
  non-empty description ≤10k chars, and a JSON schema for inputs.
- Validate inputs; return structured results; fail with clear errors.
- Flag any auth/secret requirements; never hardcode tokens.

## Agent-building tooling (alfred-agent-builder)
Scaffold a new agent, then validate:
```powershell
# scaffold from the template pattern, then:
kiro-cli agent validate --path C:\Alfred\.kiro\agents\<name>.json
```
Checklist for a new agent:
- Absolute `file:///C:/Alfred/...` paths for `prompt` and `resources`.
- Correct model tier. `allowedTools` = read-only + knowledge/todo.
- `toolsSettings.write.allowedPaths`/`deniedPaths` + shell `deniedCommands` for safety.
- A brain `identity.txt`. Registered reflexes (hooks). Entry in `.kiro/brains/README.md`.
- Validate exits 0 before the agent is considered done.
