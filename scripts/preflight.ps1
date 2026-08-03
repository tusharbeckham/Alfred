<#
.SYNOPSIS
  Alfred preflight - a fast, offline, credit-free self-check of the Alfred repo itself.
.DESCRIPTION
  Runs the local, deterministic gates and aggregates their verdicts:
    1. Workflow engine unit tests   (python scripts/test_workflow.py)
    2. Model backend unit tests      (python scripts/test_backends.py)
    3. Ultron CLI unit tests         (python scripts/test_ultron.py)
    4. MCP server security tests     (python scripts/test_mcp_server.py - slowest gate,
                                      drives the real server over stdio JSON-RPC)
    5. Workflow spec validation      (every workflows/*.json, --check-agents)
    6. Safety lint                   (scripts/safety-lint.ps1, fail on High)
    7. Dependency + hygiene audit    (scripts/dep-audit.ps1, fail on tracked secrets)
  No agents are spawned and nothing costs Kiro credits. Exit 0 only if every gate passes -
  wire it into a pre-commit hook or run it before you push.
.EXAMPLE  powershell -File scripts\preflight.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$results = @()

function Step($name, $script) {
  Write-Host "`n=== $name ===" -ForegroundColor Cyan
  & $script
  $code = $LASTEXITCODE
  if ($null -eq $code) { $code = 0 }
  $script:results += [pscustomobject]@{ Gate = $name; Exit = $code }
}

Step 'Workflow engine tests' { python scripts\test_workflow.py 2>&1 | Select-Object -Last 3 }

# backends.py is the seam every front end dispatches through, so its suite gates too.
# Fully offline: the HTTP paths are mocked, so this never touches the network.
Step 'Model backend tests' { python scripts\test_backends.py 2>&1 | Select-Object -Last 3 }

# Ultron was refactored onto backends.py; this pre-existing suite is the regression
# guard proving that refactor kept the CLI's behaviour. Fast (~0.02s).
Step 'Ultron CLI tests' { python scripts\test_ultron.py 2>&1 | Select-Object -Last 3 }

# The MCP server is reachable by any MCP client and several of its tools spawn
# subprocesses, so its input guards are a security boundary. Slowest gate (~40s):
# it drives the real server over stdio rather than mocking it. Skips if node is absent.
Step 'MCP server security tests' { python scripts\test_mcp_server.py 2>&1 | Select-Object -Last 3 }

Step 'Workflow spec validation' {
  $bad = 0
  Get-ChildItem 'workflows' -Filter '*.json' -File | ForEach-Object {
    python scripts\workflow.py validate $_.FullName --check-agents
    if ($LASTEXITCODE -ne 0) { $bad++ }
  }
  if ($bad -gt 0) { $global:LASTEXITCODE = 1 } else { $global:LASTEXITCODE = 0 }
}

Step 'Safety lint' { powershell -NoProfile -ExecutionPolicy Bypass -File scripts\safety-lint.ps1 -FailOn High | Select-Object -Last 2 }

Step 'Dependency + hygiene audit' { powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dep-audit.ps1 | Select-Object -Last 1 }

Write-Host "`n==================== PREFLIGHT SUMMARY ====================" -ForegroundColor White
$fail = 0
foreach ($r in $results) {
  $ok = $r.Exit -eq 0
  if (-not $ok) { $fail++ }
  $tag = if ($ok) { 'PASS' } else { 'FAIL' }
  $c = if ($ok) { 'Green' } else { 'Red' }
  Write-Host ("  [{0}] {1}" -f $tag, $r.Gate) -ForegroundColor $c
}
if ($fail -gt 0) { Write-Host "[preflight] FAIL - $fail gate(s) failed." -ForegroundColor Red; exit 1 }
Write-Host "[preflight] PASS - all gates green." -ForegroundColor Green
exit 0
