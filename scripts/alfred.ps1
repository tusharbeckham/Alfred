<#
.SYNOPSIS
  Alfred (OFFLINE) — call your local Alfred-Coder from any terminal. No Kiro, no credits.

.DESCRIPTION
  Ensures LM Studio's server + model are running, then runs your task on the local model
  (Granite 4.1 8B) via scripts/local-coder.ps1 — or opens an interactive local chat.
  This is the "call Alfred offline" entry point. For full team orchestration, use Kiro.

.EXAMPLE
  powershell -File scripts\alfred.ps1 "write a python function to reverse a string"
.EXAMPLE
  powershell -File scripts\alfred.ps1 -ContextFile .\app.py "add error handling"
.EXAMPLE
  powershell -File scripts\alfred.ps1 -Chat        # interactive local chat
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Task,
    [string]$Model = 'granite-4.1-8b',
    [string]$ContextFile,
    [switch]$Chat,
    [switch]$ShowStats
)

$ErrorActionPreference = 'Stop'
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
    Write-Host 'Usage:  alfred "your task"   |   alfred -Chat   |   alfred -ContextFile file "task"' -ForegroundColor Yellow
    exit 2
}
$prompt = ($Task -join ' ')
$fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'local-coder.ps1'), '-Model', $Model, $prompt)
if ($ContextFile) { $fwd += @('-ContextFile', $ContextFile) }
if ($ShowStats)   { $fwd += '-ShowStats' }
& powershell @fwd
exit $LASTEXITCODE
