<#
.SYNOPSIS
  Alfred-ify a project: generate project-scoped copies of the Alfred agents into <project>\.kiro\agents.

.DESCRIPTION
  Each generated agent REUSES the shared brains + memory in C:\Alfred (absolute file:/// paths),
  but its write scope is retargeted to the PROJECT folder and Alfred's relative hooks are dropped
  (they don't resolve outside C:\Alfred). Result: run `kiro-cli chat --agent alfred-manager` from
  the project and the full team works ON the project's files, with one shared set of personas.

.PARAMETER Project  Target project folder (e.g., C:\projects\solar-forecast).
.PARAMETER Agents   Optional subset of agent names; default = a curated dev team.
.EXAMPLE
  powershell -NoProfile -File scripts\new-project-agents.ps1 -Project C:\projects\solar-forecast
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string[]]$Agents
)
$ErrorActionPreference = 'Stop'
$src = 'C:\Alfred\.kiro\agents'
if (-not (Test-Path $Project)) { Write-Host "Project folder not found: $Project" -ForegroundColor Red; exit 1 }
$dstDir = Join-Path $Project '.kiro\agents'
New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
$projFwd = ($Project.TrimEnd('\')) -replace '\\', '/'

if (-not $Agents -or $Agents.Count -eq 0) {
  $Agents = @('alfred-manager','alfred-leader','alfred-architect','alfred-coder','alfred-tester',
              'alfred-reviewer','alfred-researcher','alfred-debugger','alfred-docs','alfred-data',
              'alfred-ml','local-coder')
}

$count = 0
foreach ($a in $Agents) {
  $f = Join-Path $src "$a.json"
  if (-not (Test-Path $f)) { Write-Host "  skip (not found): $a" -ForegroundColor Yellow; continue }
  $j = Get-Content -LiteralPath $f -Raw | ConvertFrom-Json
  # Retarget write scope to the project (reads of C:\Alfred brains/memory are unaffected)
  if ($j.toolsSettings -and $j.toolsSettings.write) {
    $j.toolsSettings.write.allowedPaths = @("$projFwd/**")
  }
  # Drop Alfred's relative hooks (they only resolve inside C:\Alfred)
  if ($j.PSObject.Properties.Name -contains 'hooks') { $j.PSObject.Properties.Remove('hooks') }
  ($j | ConvertTo-Json -Depth 15) | Set-Content -LiteralPath (Join-Path $dstDir "$a.json") -Encoding UTF8
  Write-Host "  wrote $a" -ForegroundColor Cyan
  $count++
}
# Copy shared steering (always-on rules: safety, identity, conventions, routing, memory, web, voice)
$ks = Join-Path $Project '.kiro\steering'; New-Item -ItemType Directory -Force -Path $ks | Out-Null
Copy-Item 'C:\Alfred\.kiro\steering\*.md' $ks -Force
# Copy on-demand skills so skill:// references resolve in the project
$kk = Join-Path $Project '.kiro\skills'; New-Item -ItemType Directory -Force -Path $kk | Out-Null
Copy-Item 'C:\Alfred\.kiro\skills\*' $kk -Recurse -Force
Write-Host ("Done: $count agents + steering + skills -> " + (Join-Path $Project '.kiro')) -ForegroundColor Green
