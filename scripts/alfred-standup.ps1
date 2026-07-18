<#
.SYNOPSIS
  Alfred standup - generate a work summary from git history + memory. Offline, credit-free.
.DESCRIPTION
  Builds a concise standup/report from local signals only (no agent spawned): commits in a
  window, recent decisions, and open backlog + approvals. Complements morning-report.ps1
  (which spawns alfred-manager) with a zero-cost version you can run any time.
.PARAMETER Since  Git date expression for the window. Default '1 day ago'. E.g. '1 week ago'.
.PARAMETER Save   Also write the report to memory/standup-<date>.md.
.EXAMPLE  powershell -File scripts\alfred-standup.ps1
.EXAMPLE  powershell -File scripts\alfred-standup.ps1 -Since '1 week ago' -Save
#>
[CmdletBinding()]
param(
  [string]$Since = '1 day ago',
  [switch]$Save
)
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'

$lines = @()
$lines += "# Alfred Standup"
$lines += "_Window: since $Since - generated $(Get-Date -Format 'yyyy-MM-dd HH:mm')_"
$lines += ""

# -- Shipped (commits in window) ------------------------------------------------------
$commits = @(git log --since="$Since" --pretty='%h %s' 2>$null)
$lines += "## Shipped ($($commits.Count) commit(s))"
if ($commits.Count -gt 0) { $commits | ForEach-Object { $lines += "- $_" } }
else { $lines += "- (no commits in window)" }
$lines += ""

# -- Files changed --------------------------------------------------------------------
$files = @(git log --since="$Since" --name-only --pretty=format: 2>$null | Where-Object { $_ } | Sort-Object -Unique)
$lines += "## Files touched ($($files.Count))"
if ($files.Count -gt 0) { $files | Select-Object -First 20 | ForEach-Object { $lines += "- $_" } }
else { $lines += "- (none)" }
$lines += ""

# -- Recent decisions -----------------------------------------------------------------
$dec = 'memory\decisions.md'
if (Test-Path $dec) {
  $heads = @(Get-Content $dec | Where-Object { $_ -match '^#{1,3}\s' } | Select-Object -Last 5)
  $lines += "## Recent decisions (memory/decisions.md)"
  if ($heads.Count -gt 0) { $heads | ForEach-Object { $lines += "- $($_ -replace '^#+\s*','')" } }
  else { $lines += "- (none logged)" }
  $lines += ""
}

# -- Open items + approvals -----------------------------------------------------------
$todo = 'memory\todo.md'
if (Test-Path $todo) {
  $t = Get-Content $todo
  $open = @($t | Where-Object { $_ -match '^\s*-\s*\[\s\]' })
  $appr = @($t | Where-Object { $_ -match '^\s*-\s*\[!\]' })
  $lines += "## Open backlog ($($open.Count)) + approvals ($($appr.Count))"
  $open | Select-Object -First 8 | ForEach-Object { $lines += "- [ ] " + ($_ -replace '^\s*-\s*\[\s\]\s*','') }
  $appr | Select-Object -First 8 | ForEach-Object { $lines += "- [NEEDS OWNER] " + ($_ -replace '^\s*-\s*\[!\]\s*','') }
  $lines += ""
}

$text = $lines -join "`r`n"
Write-Output $text
if ($Save) {
  $out = "memory\standup-$(Get-Date -Format 'yyyy-MM-dd').md"
  Set-Content -LiteralPath $out -Value $text -Encoding UTF8
  Write-Host "`n[standup] saved -> $out" -ForegroundColor Green
}
