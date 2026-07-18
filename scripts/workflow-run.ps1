<#
.SYNOPSIS
  Alfred workflow launcher - plan or run a declarative multi-agent DAG workflow.

.DESCRIPTION
  Thin, ergonomic wrapper over scripts/workflow.py (the engine). Resolves a bare
  workflow name to workflows/<name>.json, and defaults to a SAFE dry run: it prints
  the execution plan and previews each stage without spawning any agent. Pass
  -Execute to actually run the stages via `kiro-cli chat --no-interactive`.

.PARAMETER Workflow  Workflow name (e.g. 'feature') or a path to a spec .json.
.PARAMETER Task      The overall objective handed to the pipeline.
.PARAMETER Execute   Actually run agents. Omit for a dry run (recommended first).
.PARAMETER Plan      Only print the parallel execution plan and exit.
.PARAMETER Graph     Only print a Mermaid diagram of the DAG and exit.
.PARAMETER Var       One or more 'key=value' overrides for {vars.key} placeholders.

.EXAMPLE
  powershell -File scripts\workflow-run.ps1 -Workflow feature -Task "Add pagination to /users"
.EXAMPLE
  powershell -File scripts\workflow-run.ps1 -Workflow bugfix -Task "Login 500s on empty body" -Execute
.EXAMPLE
  powershell -File scripts\workflow-run.ps1 -Workflow audit -Plan
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Workflow,
  [string]$Task = "",
  [switch]$Execute,
  [switch]$Plan,
  [switch]$Graph,
  [string[]]$Var = @()
)
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Alfred'

# Resolve a bare name to workflows/<name>.json.
$spec = $Workflow
if (-not (Test-Path -LiteralPath $spec)) {
  $candidate = Join-Path 'workflows' ($Workflow + '.json')
  if (Test-Path -LiteralPath $candidate) { $spec = $candidate }
  else { Write-Error "Workflow spec not found: '$Workflow' (looked for $candidate)"; exit 1 }
}

if ($Graph) { python scripts\workflow.py graph $spec; exit $LASTEXITCODE }
if ($Plan)  { python scripts\workflow.py plan  $spec; exit $LASTEXITCODE }

# Always validate before running.
python scripts\workflow.py validate $spec --check-agents
if ($LASTEXITCODE -ne 0) { Write-Error "Spec failed validation."; exit 1 }

$runArgs = @('scripts\workflow.py', 'run', $spec, '--task', $Task)
foreach ($v in $Var) { $runArgs += @('--var', $v) }
if ($Execute) {
  Write-Host "[workflow-run] EXECUTE mode - stages will spawn real agents." -ForegroundColor Yellow
  $runArgs += '--execute'
} else {
  Write-Host "[workflow-run] DRY-RUN mode - add -Execute to run for real." -ForegroundColor Cyan
}
python @runArgs
exit $LASTEXITCODE
