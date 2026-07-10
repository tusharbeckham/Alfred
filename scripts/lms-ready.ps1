<#
.SYNOPSIS
  Ensure LM Studio's local server is running AND a model is loaded - auto-start + load + POLL until
  actually ready (or timeout). Idempotent; safe to call before any local-model command. Exit 0 = ready.
.DESCRIPTION
  Fixes "offline command fails on a cold start": any script needing the local model calls this first,
  so the server is started and the model loaded on demand, with a REAL readiness wait (not a fixed
  sleep). Exit codes: 0 ready | 3 server won't start | 4 model won't load | 5 LM Studio not installed.
.EXAMPLE
  powershell -NoProfile -File scripts/lms-ready.ps1 -Model alfred-coder-7b
#>
[CmdletBinding()]
param(
  [string]$Model      = 'alfred-coder-7b',
  [string]$BaseUrl    = 'http://localhost:1234/v1',
  [int]$TimeoutSec    = 45,
  [switch]$Quiet
)
$ErrorActionPreference = 'SilentlyContinue'
function Say($m) { if (-not $Quiet) { Write-Host $m -ForegroundColor DarkGray } }
function Get-LoadedModels { try { @((Invoke-RestMethod "$BaseUrl/models" -TimeoutSec 4).data.id) } catch { $null } }

$lms = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
$deadline = (Get-Date).AddSeconds($TimeoutSec)

# 1) Server up? If not, start it and poll until it responds.
$models = Get-LoadedModels
if ($null -eq $models) {
  if (-not (Test-Path $lms)) { Write-Error "LM Studio CLI not found at $lms - install LM Studio (docs/local-coder/LM-STUDIO-SETUP.md)."; exit 5 }
  Say "Alfred: starting the local model server..."
  & $lms server start *> $null
  while ((Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 700; $models = Get-LoadedModels; if ($null -ne $models) { break } }
  if ($null -eq $models) { Write-Error "LM Studio server did not respond within ${TimeoutSec}s."; exit 3 }
}

# 2) Model loaded? If not, load it and poll until it appears.
if ($models -notcontains $Model) {
  if (-not (Test-Path $lms)) { Write-Error "LM Studio CLI not found at $lms."; exit 5 }
  Say "Alfred: loading model '$Model'..."
  & $lms load $Model -y *> $null
  while ((Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 700; $models = Get-LoadedModels; if ($models -contains $Model) { break } }
  if ($models -notcontains $Model) { Write-Error ("Model '$Model' did not load within ${TimeoutSec}s. Loaded: " + ($models -join ', ') + "."); exit 4 }
}

Say "Alfred: local model ready ($Model)."
exit 0
