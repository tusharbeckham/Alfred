# Alfred — Run evals only (score, no prompt rewrite).
# Usage: powershell -File scripts\run-eval-loop.ps1 [-Suite coding|qa|all]
param([string]$Suite = "all")
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$directive = "Run the '$Suite' eval suite(s) in evals/ against the current prompts. Score " +
  "each case with evals/rubric.json. Write raw results to evals/results/<timestamp>.json and " +
  "print per-category pass rates plus the failing case ids. Do not modify any prompts."
Write-Host "[eval] start $(Get-Date -Format o) suite=$Suite"
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-evaluator $directive
Write-Host "[eval] end   $(Get-Date -Format o) exit=$LASTEXITCODE"
