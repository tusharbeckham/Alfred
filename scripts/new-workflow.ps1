<#
.SYNOPSIS
  Alfred new-workflow - scaffold a new, valid workflow spec under workflows/ and validate it.
.DESCRIPTION
  Generates workflows/<name>.json from a list of "stage:agent" pairs (sequential deps by
  default), then runs the engine validator (--check-agents). Fills tasks with TODO placeholders
  and the available template variables so you can flesh it out. Read-only except for the one
  spec file it writes.
.PARAMETER Name         Workflow name (also the file name). Required.
.PARAMETER Stages       Ordered "stagename:agent" pairs. Defaults to a plan/build/review skeleton.
.PARAMETER Description  One-line description for the spec.
.PARAMETER Force        Overwrite an existing spec of the same name.
.EXAMPLE  powershell -File scripts\new-workflow.ps1 -Name deploy
.EXAMPLE  powershell -File scripts\new-workflow.ps1 -Name hotfix -Stages "triage:alfred-debugger","fix:alfred-coder","verify:alfred-tester"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Name,
  [string[]]$Stages = @('plan:alfred-planner','build:alfred-coder','review:alfred-reviewer'),
  [string]$Description = 'TODO: describe this workflow',
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Alfred'

# Robust against callers that pass the whole list as one comma-joined string
# (common when invoked via `powershell -File ...` from another shell) or with stray quotes.
if ($Stages.Count -eq 1 -and $Stages[0] -match ',') { $Stages = $Stages[0] -split ',' }
$Stages = @($Stages | ForEach-Object { $_.Trim().Trim('"').Trim("'").Trim() } | Where-Object { $_ })

if ($Name -notmatch '^[a-zA-Z0-9._-]+$') { Write-Error "Name must be filename-safe (letters/digits/._-)."; exit 1 }
$path = Join-Path 'workflows' ($Name + '.json')
if ((Test-Path $path) -and -not $Force) { Write-Error "$path already exists. Use -Force to overwrite."; exit 1 }

function Esc($s) { ($s -replace '\\','\\' -replace '"','\"') }

$stageJson = @()
for ($i = 0; $i -lt $Stages.Count; $i++) {
  $parts = $Stages[$i].Split(':', 2)
  if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) {
    Write-Error "Bad stage '$($Stages[$i])' - expected 'stagename:agent'."; exit 1
  }
  $sName = Esc $parts[0].Trim()
  $agent = Esc $parts[1].Trim()
  $dep = if ($i -eq 0) { '' } else { '"' + (Esc $Stages[$i-1].Split(':',2)[0].Trim()) + '"' }
  $task = "TODO: what '$sName' should do. Placeholders: {task} (objective), {deps} (all deps), {stage.NAME}, {vars.KEY}."
  $stageJson += @"
    {
      "name": "$sName",
      "agent": "$agent",
      "task": "$(Esc $task)",
      "depends_on": [$dep]
    }
"@
}

$json = @"
{
  "name": "$(Esc $Name)",
  "description": "$(Esc $Description)",
  "stages": [
$($stageJson -join ",`n")
  ]
}
"@

$abs = Join-Path (Get-Location).Path $path
[System.IO.File]::WriteAllText($abs, $json)  # UTF-8 without BOM
Write-Host "[new-workflow] wrote $path" -ForegroundColor Green

Write-Host "[new-workflow] validating..." -ForegroundColor Cyan
python scripts\workflow.py validate $path --check-agents
if ($LASTEXITCODE -ne 0) {
  Write-Host "[new-workflow] spec written but did NOT validate - fix the agent names or structure above." -ForegroundColor Yellow
  exit 1
}
Write-Host "[new-workflow] Next: edit the task fields, then:" -ForegroundColor DarkGray
Write-Host "  powershell -File scripts\workflow-run.ps1 -Workflow $Name -Plan" -ForegroundColor DarkGray
Write-Host "  powershell -File scripts\workflow-run.ps1 -Workflow $Name -Task ""...""" -ForegroundColor DarkGray
exit 0
