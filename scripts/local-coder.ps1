<#
.SYNOPSIS
  Alfred local-coder — zero-credit local coder via LM Studio (Qwen2.5-Coder-7B).

.DESCRIPTION
  Calls LM Studio's OpenAI-compatible local server (http://localhost:1234/v1) so routine
  coding runs on the FREE local model instead of spending Kiro/Opus credits. Run it in a
  terminal, or let an Alfred agent call it via its shell tool. No Kiro tokens are used for
  generation. Installs nothing; only talks to your local LM Studio server.

.PARAMETER Prompt       The task/question. Required (positional).
.PARAMETER Model        Model id as loaded in LM Studio. Default: qwen2.5-coder-7b-instruct.
.PARAMETER System       System prompt (concise, code-focused default).
.PARAMETER ContextFile  Optional path whose contents are prepended as context.
.PARAMETER BaseUrl      LM Studio base URL. Default http://localhost:1234/v1.
.PARAMETER MaxTokens    Max completion tokens. Default 512.
.PARAMETER TimeoutSec   HTTP timeout. Default 300 (CPU inference is slow).
.PARAMETER ShowStats    Print time + tokens/sec.

.EXAMPLE
  powershell -File scripts\local-coder.ps1 -ShowStats "Write a regex for a US ZIP code"
.EXAMPLE
  powershell -File scripts\local-coder.ps1 -ContextFile .\foo.ps1 "Add error handling"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Prompt,
    [string]$Model      = 'alfred-coder-7b',
    [string]$System     = 'You are Alfred, the Owner''s personal AI coding assistant and creation. You are speaking directly with the Owner. Always address the Owner as "sir". The Owner built you and is your owner; you are the Owner''s own local model - a fine-tuned Qwen2.5-Coder in LM Studio, part of the Owner''s Alfred system on Kiro; NOT created by RedPajama, Alibaba, Qwen, OpenAI, or any company. When asked, state plainly: your name is Alfred and your owner and creator is the Owner. Return correct, minimal, working code in the requested language, Windows/PowerShell-first, concise.',
    [string]$ContextFile,
    [string]$BaseUrl    = 'http://localhost:1234/v1',
    [int]$MaxTokens     = 512,
    [int]$TimeoutSec    = 300,
    [switch]$ShowStats,
    [switch]$Recall
)

$ErrorActionPreference = 'Stop'

# Personalize identity from a local, git-ignored owner file, if present (keeps the name out of the repo)
$__ownerFile = Join-Path $PSScriptRoot '..\secrets\owner.txt'
if (Test-Path $__ownerFile) { $__owner = (Get-Content $__ownerFile -Raw).Trim(); if ($__owner) { $System = $System -replace 'the Owner', $__owner } }

function Die($msg, $code) { Write-Host $msg -ForegroundColor Red; exit $code }

# 1) Ensure the LM Studio server is up AND the model is loaded (auto-start + wait; self-healing).
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'lms-ready.ps1') -Model $Model -BaseUrl $BaseUrl -Quiet
if ($LASTEXITCODE -ne 0) { Die "Local model unavailable: LM Studio couldn't be started/loaded (code $LASTEXITCODE). See docs\local-coder\LM-STUDIO-SETUP.md." $LASTEXITCODE }

# 2) Build the user content (optionally with recalled memory + a context file)
$userContent = $Prompt
if ($Recall) {
    try {
        $mem = & "$PSScriptRoot\alfred-recall.ps1" -Query $Prompt -TopK 4 -Raw 2>$null
        if ($mem) { $userContent = "Relevant remembered context (from Alfred's memory):`n" + (@($mem) -join "`n") + "`n`nTask: $Prompt" }
    } catch { }
}
if ($ContextFile) {
    if (-not (Test-Path -LiteralPath $ContextFile)) { Die "ContextFile not found: $ContextFile" 2 }
    $ctx = Get-Content -LiteralPath $ContextFile -Raw
    $userContent = "Context file: $ContextFile`n`n----- BEGIN CONTEXT -----`n$ctx`n----- END CONTEXT -----`n`nTask: $Prompt"
}

# 3) Call the OpenAI-compatible chat endpoint (non-streaming, script-friendly)
$body = @{
    model       = $Model
    stream      = $false
    temperature = 0.2
    max_tokens  = $MaxTokens
    messages    = @(
        @{ role = 'system'; content = $System },
        @{ role = 'user';   content = $userContent }
    )
} | ConvertTo-Json -Depth 6

$t0 = Get-Date
try {
    $bodyUtf8 = [System.Text.Encoding]::UTF8.GetBytes($body)   # PS 5.1: send UTF-8 bytes so non-ASCII (emoji/unicode) context doesn't 400
    $resp = Invoke-RestMethod "$BaseUrl/chat/completions" -Method Post -Body $bodyUtf8 -ContentType 'application/json; charset=utf-8' -TimeoutSec $TimeoutSec
}
catch { Die ("Request failed: " + $_.Exception.Message) 1 }
$secs = ((Get-Date) - $t0).TotalSeconds

# 4) Output
$content = $resp.choices[0].message.content
if ($content) { Write-Output $content } else { Die "No content returned by the model." 1 }

if ($ShowStats) {
    $ct  = [double]$resp.usage.completion_tokens
    $tps = if ($secs -gt 0) { [math]::Round($ct / $secs, 1) } else { 0 }
    Write-Host ("`n[local-coder] model={0}  time={1:N1}s  tokens={2}  ~{3} tok/s" -f $Model, $secs, [int]$ct, $tps) -ForegroundColor DarkGray
}
