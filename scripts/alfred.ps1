<#
.SYNOPSIS
  Alfred (OFFLINE) — call your local Alfred-Coder from any terminal, plus a deterministic
  git commit+push. No Kiro, no credits.

.DESCRIPTION
  - alfred "task"        -> run a coding task on the local model (Alfred-Coder via LM Studio)
  - alfred -Chat         -> interactive local chat
  - alfred -Push "msg"   -> git add -A + commit + push in the CURRENT folder (NO model needed)

.EXAMPLE  powershell -File scripts\alfred.ps1 "write a python function to reverse a string"
.EXAMPLE  powershell -File scripts\alfred.ps1 -Push "update readme"
.EXAMPLE  powershell -File scripts\alfred.ps1 -Chat
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Task,
    [string]$Model = 'alfred-coder-7b',
    [string]$ContextFile,
    [switch]$Chat,
    [switch]$Push,
    [switch]$ShowStats
)
$ErrorActionPreference = 'Stop'

# -Push: deterministic git add + commit + push in the CURRENT directory (no model involved).
if ($Push) {
    $ErrorActionPreference = 'Continue'   # git prints warnings (e.g. LF/CRLF) to stderr; keep them non-fatal so the commit+push completes
    $null = git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host ("Not a git repository: " + (Get-Location).Path) -ForegroundColor Red; exit 1 }
    $msg = if ($Task -and $Task.Count -gt 0) { $Task -join ' ' } else { 'update' }
    git add -A 2>$null
    if (-not (git diff --cached --name-only 2>$null)) { Write-Host "Nothing to commit." -ForegroundColor Yellow; exit 0 }
    git commit -m $msg 2>$null | Out-Null
    git push 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host ("Pushed: " + $msg) -ForegroundColor Green }
    else { Write-Host ("Push failed (exit " + $LASTEXITCODE + ")") -ForegroundColor Red }
    exit $LASTEXITCODE
}

$lms = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
if (-not (Test-Path $lms)) {
    Write-Host "LM Studio CLI not found. Install LM Studio first (see docs\local-coder\LM-STUDIO-SETUP.md)." -ForegroundColor Red
    exit 1
}

# 1+2) Ensure the server is up AND the model is loaded (auto-start + poll until ready; self-healing).
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'lms-ready.ps1') -Model $Model
if ($LASTEXITCODE -ne 0) { Write-Host "Alfred: local model unavailable (LM Studio couldn't start/load). See docs\local-coder\LM-STUDIO-SETUP.md." -ForegroundColor Red; exit 1 }

# 3) Interactive chat mode (pin Alfred's identity so it never confabulates a creator)
$AlfredSys = 'You are Alfred, the Owner''s personal AI assistant and creation. You are speaking directly with the Owner. Always address the Owner as "sir". The Owner built you and is your owner; you are the Owner''s own local model - a fine-tuned Qwen2.5-Coder in LM Studio, part of the Owner''s Alfred system on Kiro. You were NOT created by RedPajama, Alibaba, Qwen, OpenAI, or any company. When asked, state plainly: your name is Alfred and your owner and creator is the Owner (whom you address as sir). Be concise, precise, and honest.'
$__ownerFile = Join-Path $PSScriptRoot '..\secrets\owner.txt'
if (Test-Path $__ownerFile) { $__owner = (Get-Content $__ownerFile -Raw).Trim(); if ($__owner) { $AlfredSys = $AlfredSys -replace 'the Owner', $__owner } }
if ($Chat) { & $lms chat $Model -s $AlfredSys; exit $LASTEXITCODE }

# 4) One-off task -> local-coder.ps1
if (-not $Task -or $Task.Count -eq 0) {
    Write-Host 'Usage:  alfred "your task"   |   alfred -Chat   |   alfred -Push "msg"   |   alfred -ContextFile file "task"' -ForegroundColor Yellow
    exit 2
}
$prompt = ($Task -join ' ')
$fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'local-coder.ps1'), '-Model', $Model, $prompt)
if ($ContextFile) { $fwd += @('-ContextFile', $ContextFile) }
if ($ShowStats)   { $fwd += '-ShowStats' }
& powershell @fwd
exit $LASTEXITCODE
