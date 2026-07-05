# Alfred — CI gate. Detects the stack and runs lint/build/test; writes the verdict.
# Usage: powershell -File scripts\ci-run.ps1 [-ProjectPath C:\path\to\project]
# Exit code mirrors the CI verdict (0 = PASS, 1 = FAIL) for use in other scripts.
param([string]$ProjectPath = 'C:\Alfred')
$ErrorActionPreference = 'Continue'
$directive = "Act as the CI gate per prompts/ci-cd/ci-run.txt for the project at " +
  "'$ProjectPath'. Detect the stack, run install/lint/type-check/build/tests (stop at first " +
  "hard failure), write a per-stage summary to C:\Alfred\memory\ci-results.md, and make the " +
  "LAST line exactly 'CI: PASS' or 'CI: FAIL'."
Write-Host "[ci] start $(Get-Date -Format o) project=$ProjectPath"
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-devops $directive
# Reflect the verdict from the results file as this script's exit code.
$verdict = Get-Content 'C:\Alfred\memory\ci-results.md' -ErrorAction SilentlyContinue |
  Where-Object { $_ -match 'CI:\s*(PASS|FAIL)' } | Select-Object -Last 1
if ($verdict -match 'CI:\s*PASS') { Write-Host "[ci] PASS"; exit 0 }
else { Write-Host "[ci] FAIL"; exit 1 }
