# Alfred agent template

> Alfred agents are **JSON** files in `.kiro/agents/<name>.json` (not `.md`). This file
> documents that verified format so new agents match the existing 19 + local-coder.
> To scaffold one automatically: `powershell -File scripts\spawn-agent.ps1 -Name <x> -Role "<role>"`.

## Rules that make an agent valid (learned the hard way — see `memory/decisions.md`)

1. **Absolute `file://` URIs.** `prompt` and every `resources` entry (including a
   knowledgeBase `source`) MUST use `file:///C:/Alfred/...`. Relative `file://` paths
   resolve against `.kiro/agents/`, not the repo root, and break.
2. **Model tier.** `claude-opus-4.8` (manager/leader), `claude-opus-4.6` (workers/meta),
   `claude-sonnet-4.6` (planner/prompt-engineer/lightweight dispatchers). An unknown model
   ID silently falls back to the default (`claude-opus-4.8`) — so never invent a model name
   (e.g. `ollama/...`); it would quietly run on Opus.
3. **Least privilege.** `allowedTools` = read-only + knowledge (auto-approved). Put write
   behind `allowedPaths`/`deniedPaths`; put shell behind `deniedCommands` (and optionally
   `allowedCommands` / `denyByDefault` for tightly-scoped agents like pc-ops).
4. **Shared memory.** Include the `alfred-shared-memory` knowledgeBase resource.
5. **Reflexes.** Wire the standard hooks (`pre-write`, `post-shell`, `stop`) as appropriate.
6. **Validate:** `kiro-cli agent validate --path C:\Alfred\.kiro\agents\<name>.json` (exit 0),
   then add a row to `.kiro/brains/README.md`.

## Skeleton

```json
{
  "name": "<agent-name>",
  "description": "<one line: what this agent is and does>",
  "model": "claude-opus-4.6",
  "prompt": "file:///C:/Alfred/.kiro/brains/<agent-name>/identity.txt",
  "tools": ["read", "write", "shell", "grep", "glob", "knowledge"],
  "allowedTools": ["read", "grep", "glob", "knowledge"],
  "toolsSettings": {
    "write": {
      "allowedPaths": ["C:/Alfred/**"],
      "deniedPaths": ["C:/Windows/**", "**/.env", "**/secrets/**", "**/*.key", "**/*.pem", "C:/Alfred/.kiro/agents/**"]
    },
    "shell": {
      "autoAllowReadonly": true,
      "deniedCommands": ["rm\\s+-rf", "format\\s", "reg\\s+(add|delete)", "Remove-Item.*-Recurse", "git\\s+push.*(main|master)", "git\\s+push\\s+--force", "git\\s+reset\\s+--hard", "git\\s+clean\\s+-", "shutdown", "Stop-Computer"]
    }
  },
  "resources": [
    { "type": "knowledgeBase", "source": "file:///C:/Alfred/memory", "name": "alfred-shared-memory", "indexType": "best", "include": ["**/*.md"], "autoUpdate": true }
  ],
  "hooks": {
    "preToolUse": [ { "matcher": "write", "command": "powershell -NoProfile -ExecutionPolicy Bypass -File hooks/pre-write.ps1", "timeout_ms": 10000 } ],
    "postToolUse": [ { "matcher": "shell", "command": "powershell -NoProfile -ExecutionPolicy Bypass -File hooks/post-shell.ps1", "timeout_ms": 10000 } ],
    "stop": [ { "command": "powershell -NoProfile -ExecutionPolicy Bypass -File hooks/on-stop.ps1", "timeout_ms": 15000 } ]
  }
}
```

## Also create the brain identity

`.kiro/brains/<agent-name>/identity.txt` — the system prompt (Layer 1). Keep it to: who the
agent is, how it works (numbered steps), what it must NOT do, and its escalation rule.

## Worked example

`local-coder` is the reference for a **local-model dispatcher**: cheap `sonnet` driver,
tightly scoped shell (only its own script + read-only `ollama` queries), write denied on
`.kiro/agents|steering|settings` and `hooks`, and an identity that mandates generating code
via `scripts/local-coder.ps1` (local Ollama) instead of a premium model.
