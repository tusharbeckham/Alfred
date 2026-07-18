<#
.SYNOPSIS
  Alfred security-report - run the whole defensive toolchain and write one consolidated report.
.DESCRIPTION
  Orchestrates Alfred's read-only security tools over a target and writes a dated markdown
  report to memory/. Bundles:
    * security-scan.ps1  - exposed-secret content scan (redacted)
    * safety-lint.ps1    - dangerous-command static lint (enforces safety.md)
    * dep-audit.ps1      - dependency + repo-hygiene audit
    * security-audit.ps1 - Windows security posture (only with -IncludePC; it audits the
                           machine, not the repo)
  Overall exit code is 1 if any bundled tool failed (non-zero), else 0 - so it can gate CI.
  Everything is read-only and local; nothing is transmitted anywhere.
.PARAMETER Path       Target repo/folder. Default: C:\Alfred
.PARAMETER IncludePC  Also run the machine security-posture audit.
.EXAMPLE  powershell -File scripts\security-report.ps1
.EXAMPLE  powershell -File scripts\security-report.ps1 -Path C:\Projects\app -IncludePC
#>
[CmdletBinding()]
param(
  [string]$Path = 'C:\Alfred',
  [switch]$IncludePC
)
$ErrorActionPreference = 'Continue'
$scripts = 'C:\Alfred\scripts'
$stamp   = Get-Date -Format 'yyyy-MM-dd_HHmm'
$reportDir = 'C:\Alfred\memory'
$report  = Join-Path $reportDir "security-report-$stamp.md"

function Invoke-Tool($title, $file, $argList) {
  Write-Host "[security-report] running $title ..." -ForegroundColor Cyan
  $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scripts $file) @argList 2>&1 | Out-String
  $code = $LASTEXITCODE
  [pscustomobject]@{ Title = $title; Exit = $code; Output = $out }
}

$results = @()
$results += Invoke-Tool 'Secret content scan (security-scan)' 'security-scan.ps1' @('-Path', $Path)
$results += Invoke-Tool 'Safety lint (safety-lint)'          'safety-lint.ps1'   @('-Path', $Path)
$results += Invoke-Tool 'Dependency + hygiene (dep-audit)'   'dep-audit.ps1'     @('-Path', $Path)
if ($IncludePC) {
  $results += Invoke-Tool 'Windows posture (security-audit)' 'security-audit.ps1' @()
}

# -- Compose the markdown report ------------------------------------------------------
$failCount = @($results | Where-Object { $_.Exit -ne 0 }).Count
$overall = if ($failCount -eq 0) { 'PASS' } else { 'ATTENTION' }

$md = @()
$md += "# Alfred Security Report"
$md += ""
$md += "- Target: ``$Path``"
$md += "- Generated: $(Get-Date -Format o)"
$md += "- Overall: **$overall** ($failCount of $($results.Count) tool(s) reported findings/failure)"
$md += ""
$md += "| Tool | Exit | Verdict |"
$md += "|------|------|---------|"
foreach ($r in $results) {
  $verdict = if ($r.Exit -eq 0) { 'clean / within policy' } else { 'findings - review below' }
  $md += "| $($r.Title) | $($r.Exit) | $verdict |"
}
$md += ""
foreach ($r in $results) {
  $md += "## $($r.Title)"
  $md += ""
  $md += '```'
  $md += ($r.Output.TrimEnd())
  $md += '```'
  $md += ""
}

Set-Content -LiteralPath $report -Value ($md -join "`r`n") -Encoding UTF8
Write-Host ("[security-report] {0}. Wrote {1}" -f $overall, $report) -ForegroundColor $(if ($overall -eq 'PASS') {'Green'} else {'Yellow'})
if ($failCount -gt 0) { exit 1 }
exit 0
