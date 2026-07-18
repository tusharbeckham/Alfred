<#
.SYNOPSIS
  Ultron - Alfred's local CLI (Kiro-compatible agent workflow, free/offline via LM Studio).
.DESCRIPTION
  Thin launcher for scripts/ultron.py so you can run it like kiro-cli from the repo root.
.EXAMPLE
  .\ultron.ps1 agents
.EXAMPLE
  .\ultron.ps1 run --agent alfred-qa "draft a test plan for a login form"
.EXAMPLE
  .\ultron.ps1 chat --agent alfred-coder
#>
python (Join-Path $PSScriptRoot 'scripts\ultron.py') @args
exit $LASTEXITCODE
