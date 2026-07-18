<#
.SYNOPSIS
  Alfred status - a one-glance dashboard of the whole system. Read-only, offline, credit-free.
.DESCRIPTION
  Summarizes the repo at a glance: agent/skill/tool/workflow counts, git state (branch, drift
  vs origin, dirty files, last commit), memory footprint, and the open backlog + approvals from
  memory/todo.md. Nothing is spawned and nothing costs Kiro credits - it just reads the repo.
.PARAMETER Root  Repo root. Default: C:\Alfred
.EXAMPLE  powershell -File scripts\alfred-status.ps1
#>
[CmdletBinding()]
param([string]$Root = 'C:\Alfred')
$ErrorActionPreference = 'Continue'
Set-Location $Root

function Count($path, $filter, $recurse) {
  @(Get-ChildItem -LiteralPath (Join-Path $Root $path) -Filter $filter -File -Recurse:$recurse -ErrorAction SilentlyContinue).Count
}
function Section($t) { Write-Host "`n$t" -ForegroundColor Cyan }

Write-Host "==================== ALFRED STATUS ====================" -ForegroundColor White
Write-Host (" {0}   {1}" -f $env:COMPUTERNAME, (Get-Date -Format 'yyyy-MM-dd HH:mm')) -ForegroundColor DarkGray

Section "System"
$agents    = Count '.kiro/agents' '*.json' $false
$skills    = @(Get-ChildItem '.kiro/skills' -Filter 'SKILL.md' -File -Recurse -ErrorAction SilentlyContinue).Count
$steering  = Count '.kiro/steering' '*.md' $false
$ps        = Count 'scripts' '*.ps1' $true
$py        = Count 'scripts' '*.py' $true
$workflows = Count 'workflows' '*.json' $false
Write-Host ("  Agents:    {0,-4} Skills:    {1,-4} Steering: {2}" -f $agents, $skills, $steering)
Write-Host ("  Scripts:   {0} ps1 + {1} py     Workflows: {2}" -f $ps, $py, $workflows)

Section "Git"
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
$commits = (git rev-list --count HEAD 2>$null)
$dirty = @(git status --porcelain 2>$null).Count
$drift = (git rev-list --left-right --count "origin/$branch...HEAD" 2>$null)
$behindAhead = if ($drift) { $drift -replace '\s+',' / ' } else { 'n/a' }
$last = (git log -1 --pretty='%h %s (%cr)' 2>$null)
Write-Host ("  Branch: {0}   Commits: {1}   Uncommitted: {2}" -f $branch, $commits, $dirty)
Write-Host ("  Drift vs origin/$branch (behind/ahead): {0}" -f $behindAhead)
Write-Host ("  Last: {0}" -f $last) -ForegroundColor DarkGray

Section "Memory"
$mj = Join-Path $Root 'memory\memory.jsonl'
$episodic = if (Test-Path $mj) { @(Get-Content $mj).Count } else { 0 }
$dec = Join-Path $Root 'memory\decisions.md'
$decN = if (Test-Path $dec) { (Get-Content $dec | Measure-Object -Line).Lines } else { 0 }
$mm = Join-Path $Root 'memory\megamind.db'
$mmKB = if (Test-Path $mm) { [math]::Round((Get-Item $mm).Length / 1KB) } else { 0 }
Write-Host ("  Episodic entries: {0}   Megamind DB: {1} KB   decisions.md: {2} lines" -f $episodic, $mmKB, $decN)

Section "Backlog (memory/todo.md)"
$todo = Join-Path $Root 'memory\todo.md'
if (Test-Path $todo) {
  $t = Get-Content $todo
  $pending  = @($t | Where-Object { $_ -match '^\s*-\s*\[\s\]' }).Count
  $progress = @($t | Where-Object { $_ -match '^\s*-\s*\[~\]' }).Count
  $blocked  = @($t | Where-Object { $_ -match '^\s*-\s*\[!\]' }).Count
  Write-Host ("  Pending: {0}   In-progress: {1}   Needs-Owner (blocked): {2}" -f $pending, $progress, $blocked)
  if ($blocked -gt 0) {
    Write-Host "  Approvals waiting:" -ForegroundColor Yellow
    $t | Where-Object { $_ -match '^\s*-\s*\[!\]' } | Select-Object -First 5 | ForEach-Object {
      Write-Host ("    " + ($_ -replace '^\s*-\s*\[!\]\s*', '')) -ForegroundColor Yellow
    }
  }
} else { Write-Host "  (no todo.md)" }
Write-Host "`n=======================================================" -ForegroundColor White
