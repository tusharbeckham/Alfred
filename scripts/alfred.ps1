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

# 1) Ensure the server is up
try { $null = Invoke-RestMethod 'http://localhost:1234/v1/models' -TimeoutSec 3 }
catch {
    Write-Host "Alfred: starting the local model server..." -ForegroundColor DarkGray
    & $lms server start | Out-Null
    Start-Sleep -Seconds 2
}

# 2) Ensure the model is loaded
try { $loaded = @((Invoke-RestMethod 'http://localhost:1234/v1/models' -TimeoutSec 5).data.id) } catch { $loaded = @() }
if ($loaded -notcontains $Model) {
    Write-Host "Alfred: loading $Model..." -ForegroundColor DarkGray
    & $lms load $Model -y | Out-Null
}

# 3) Interactive chat mode
if ($Chat) { & $lms chat $Model; exit $LASTEXITCODE }

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
