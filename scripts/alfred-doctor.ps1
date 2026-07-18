<#
.SYNOPSIS
  Alfred doctor - environment health check for the toolchain Alfred relies on. Read-only.
.DESCRIPTION
  Verifies the things that make Alfred work: Python, kiro-cli, git (required); Node, disk space,
  LM Studio reachability, key repo files, and whether the security pre-commit hook is installed
  (optional/informational). Prints OK/WARN/BAD per check. Exits 1 only if a REQUIRED tool is
  missing, so it can gate setup scripts. Nothing is changed or transmitted.
.EXAMPLE  powershell -File scripts\alfred-doctor.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$critical = 0

function Line($label, $val, $flag) {
  $c = switch ($flag) { 'OK' {'Green'} 'WARN' {'Yellow'} 'BAD' {'Red'} default {'Gray'} }
  Write-Host ("  [{0,-4}] {1}: {2}" -f $flag, $label, $val) -ForegroundColor $c
}
function Have($cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

Write-Host "=== Alfred doctor (environment health) ===" -ForegroundColor Cyan

Write-Host "`n-- Required toolchain --"
if (Have 'python') { Line 'Python' ((python --version 2>&1) -join ' ') 'OK' }
else { Line 'Python' 'NOT FOUND' 'BAD'; $critical++ }
if (Have 'kiro-cli') { Line 'kiro-cli' ((kiro-cli --version 2>&1) -join ' ') 'OK' }
else { Line 'kiro-cli' 'NOT FOUND (offline tools still work)' 'WARN' }
if (Have 'git') { Line 'git' ((git --version 2>&1) -join ' ') 'OK' }
else { Line 'git' 'NOT FOUND' 'BAD'; $critical++ }

Write-Host "`n-- Optional toolchain --"
if (Have 'node') { Line 'Node' ((node --version 2>&1) -join ' ') 'OK' } else { Line 'Node' 'not present' 'WARN' }
if (Have 'lms')  { Line 'LM Studio CLI' 'present' 'OK' }               else { Line 'LM Studio CLI' 'not present' 'WARN' }

Write-Host "`n-- Disk (C:) --"
try {
  $d = Get-PSDrive C -ErrorAction Stop
  $freeGB = [math]::Round($d.Free / 1GB, 1)
  Line 'Free space' ("{0} GB" -f $freeGB) $(if ($freeGB -ge 10) {'OK'} elseif ($freeGB -ge 3) {'WARN'} else {'BAD'})
} catch { Line 'Disk' 'unknown' 'WARN' }

Write-Host "`n-- Local model server (LM Studio @ 127.0.0.1:1234) --"
try {
  $tcp = New-Object System.Net.Sockets.TcpClient
  $iar = $tcp.BeginConnect('127.0.0.1', 1234, $null, $null)
  if ($iar.AsyncWaitHandle.WaitOne(400) -and $tcp.Connected) { Line 'LM Studio' 'reachable' 'OK' }
  else { Line 'LM Studio' 'not reachable (local-coder path parked - OK)' 'WARN' }
  $tcp.Close()
} catch { Line 'LM Studio' 'not reachable' 'WARN' }

Write-Host "`n-- Repo integrity --"
foreach ($f in 'AGENTS.md','scripts/workflow.py','.kiro/steering/safety.md') {
  Line $f $(if (Test-Path $f) {'present'} else {'MISSING'}) $(if (Test-Path $f) {'OK'} else {'BAD'})
  if (-not (Test-Path $f)) { $critical++ }
}
$agents = @(Get-ChildItem '.kiro/agents' -Filter '*.json' -File -EA SilentlyContinue).Count
$skills = @(Get-ChildItem '.kiro/skills' -Filter 'SKILL.md' -Recurse -File -EA SilentlyContinue).Count
$wf     = @(Get-ChildItem 'workflows' -Filter '*.json' -File -EA SilentlyContinue).Count
Line 'Agents / Skills / Workflows' ("{0} / {1} / {2}" -f $agents, $skills, $wf) 'OK'

Write-Host "`n-- Security hook --"
$hookPath = (git rev-parse --git-path hooks 2>$null)
$pc = if ($hookPath) { Join-Path $hookPath 'pre-commit' } else { '' }
if ($pc -and (Test-Path $pc) -and ((Get-Content -Raw $pc) -match 'ALFRED-HOOK')) {
  Line 'pre-commit scan' 'installed' 'OK'
} else {
  Line 'pre-commit scan' 'not installed (run scripts/install-git-hooks.ps1)' 'WARN'
}

Write-Host ""
if ($critical -gt 0) { Write-Host "[doctor] $critical critical issue(s). Alfred needs these fixed." -ForegroundColor Red; exit 1 }
Write-Host "[doctor] healthy - all required tooling present." -ForegroundColor Green
exit 0
