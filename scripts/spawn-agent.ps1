# Alfred — Scaffold + validate a new agent via alfred-agent-builder.
# Usage: powershell -File scripts\spawn-agent.ps1 -Name translator -Role "translates text between languages"
param(
  [Parameter(Mandatory=$true)][string]$Name,
  [string]$Role = ""
)
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$directive = "Scaffold a new agent named 'alfred-$Name' (role: $Role). Create its brain at " +
  ".kiro/brains/alfred-$Name/identity.txt and its config at .kiro/agents/alfred-$Name.json " +
  "following Alfred's verified pattern: absolute file:///C:/Alfred/... paths, an appropriate " +
  "model tier, allowedTools = read-only + knowledge, safety-scoped write allowedPaths/deniedPaths " +
  "and shell deniedCommands, the shared-memory knowledgeBase, and reflex hooks. Then run " +
  "'kiro-cli agent validate --path C:\Alfred\.kiro\agents\alfred-$Name.json' and fix until it " +
  "exits 0. Finally add the agent to .kiro/brains/README.md and report the validation result."
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-agent-builder $directive
Write-Host "[spawn-agent] done (exit=$LASTEXITCODE)"
