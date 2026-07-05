<#
.SYNOPSIS
  Alfred local-coder — zero-credit code assistant backed by a local Ollama model.

.DESCRIPTION
  Talks directly to the local Ollama REST API (http://localhost:11434) so routine
  coding tasks run on the FREE local model (qwen2.5-coder) instead of spending Kiro /
  Opus credits. This script is the real credit-saver: run it in a terminal, or let an
  Alfred agent call it via its shell tool. No Kiro model tokens are used for generation.

  It does NOT install anything, does NOT modify system state, and only reads/writes
  inside the task you give it (stdout). Safe to run repeatedly.

.PARAMETER Prompt
  The task / question for the local model. Required (positional).

.PARAMETER Model
  Ollama model tag. Default: qwen2.5-coder:7b.

.PARAMETER System
  Optional system prompt. A concise code-focused default is used if omitted.

.PARAMETER ContextFile
  Optional path to a file whose contents are prepended as context.

.PARAMETER Host
  Ollama base URL. Default: http://localhost:11434.

.PARAMETER TimeoutSec
  HTTP timeout. Default: 300 (CPU inference can be slow).

.PARAMETER ShowStats
  Print timing / tokens-per-second after the answer.

.EXAMPLE
  powershell -File scripts\local-coder.ps1 "Write a PowerShell function that reverses a string"

.EXAMPLE
  powershell -File scripts\local-coder.ps1 -Model qwen2.5-coder:14b -ShowStats "Refactor this" -ContextFile .\foo.ps1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Prompt,

    [string]$Model = 'qwen2.5-coder:7b',

    [string]$System = 'You are a precise, senior coding assistant. Return correct, minimal, working code. Prefer the language/style implied by the request. Add only brief, useful comments. If something is ambiguous, state your assumption in one line, then answer.',

    [string]$ContextFile,

    [string]$Host = 'http://localhost:11434',

    [int]$TimeoutSec = 300,

    [switch]$ShowStats
)

$ErrorActionPreference = 'Stop'

function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

# 1) Is Ollama up?
try {
    $null = Invoke-RestMethod -Uri "$Host/api/version" -Method Get -TimeoutSec 5
}
catch {
    Write-Err "Ollama is not reachable at $Host."
    Write-Err "Start it (it usually runs as a tray app after install) or run:  ollama serve"
    Write-Err "If Ollama is not installed yet, see docs\local-coder\SETUP.md (Owner approval required to install)."
    exit 3
}

# 2) Is the requested model present?
try {
    $tags = Invoke-RestMethod -Uri "$Host/api/tags" -Method Get -TimeoutSec 10
    $have = @($tags.models | ForEach-Object { $_.name })
    if ($have -notcontains $Model) {
        Write-Err "Model '$Model' is not pulled yet. Available: $($have -join ', ')"
        Write-Err "Pull it first (Owner approval required):  ollama pull $Model"
        exit 4
    }
}
catch {
    Write-Err "Could not list models from $Host/api/tags : $($_.Exception.Message)"
    exit 4
}

# 3) Build the user content (optionally with a context file)
$userContent = $Prompt
if ($ContextFile) {
    if (-not (Test-Path -LiteralPath $ContextFile)) {
        Write-Err "ContextFile not found: $ContextFile"
        exit 2
    }
    $ctx = Get-Content -LiteralPath $ContextFile -Raw
    $userContent = "Context file: $ContextFile`n`n----- BEGIN CONTEXT -----`n$ctx`n----- END CONTEXT -----`n`nTask: $Prompt"
}

# 4) Call the chat endpoint (non-streaming for simple, script-friendly output)
$body = @{
    model    = $Model
    stream   = $false
    messages = @(
        @{ role = 'system'; content = $System },
        @{ role = 'user';   content = $userContent }
    )
} | ConvertTo-Json -Depth 6

try {
    $resp = Invoke-RestMethod -Uri "$Host/api/chat" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec $TimeoutSec
}
catch {
    Write-Err "Request failed: $($_.Exception.Message)"
    exit 1
}

# 5) Output
if ($resp.message -and $resp.message.content) {
    Write-Output $resp.message.content
}
else {
    Write-Err "No content returned. Raw response:"
    $resp | ConvertTo-Json -Depth 6 | Write-Output
    exit 1
}

if ($ShowStats) {
    $evalCount   = [double]($resp.eval_count | ForEach-Object { $_ })
    $evalDurNs   = [double]($resp.eval_duration | ForEach-Object { $_ })
    $totalDurNs  = [double]($resp.total_duration | ForEach-Object { $_ })
    $tps = if ($evalDurNs -gt 0) { [math]::Round($evalCount / ($evalDurNs / 1e9), 1) } else { 0 }
    $totalS = if ($totalDurNs -gt 0) { [math]::Round($totalDurNs / 1e9, 1) } else { 0 }
    Write-Host ""
    Write-Host ("[local-coder] model={0}  total={1}s  gen_tokens={2}  ~{3} tok/s" -f $Model, $totalS, [int]$evalCount, $tps) -ForegroundColor DarkGray
}
