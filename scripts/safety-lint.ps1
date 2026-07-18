<#
.SYNOPSIS
  Alfred safety-lint - static analyzer that enforces safety.md across the repo's OWN scripts.
.DESCRIPTION
  Scans executable scripts (.ps1/.py/.sh/.js/.bat/.cmd) for genuinely dangerous invocations
  (recursive/force deletes, destructive git, disk format, download-and-execute, registry and
  power operations) and reports Severity + file:line + a snippet. Read-only; changes nothing.

  It deliberately matches only REAL command usage: detection regexes use actual whitespace
  (\s+), so the escaped denylist forms baked into agent configs (e.g. the deniedCommands
  entry that BLOCKS a recursive delete) are NOT flagged as usage. Add the comment token
  'safety-lint: allow' to a line to suppress an intentional, reviewed case.

  Exit code: 1 if any finding at or above -FailOn (default High) is present; else 0. Wire it
  into CI or a commit hook to keep the automation itself safe.
.PARAMETER Path    Root to scan. Default: C:\Alfred
.PARAMETER FailOn  Minimum severity that fails the run: High | Medium | Low. Default High.
.EXAMPLE  powershell -File scripts\safety-lint.ps1
.EXAMPLE  powershell -File scripts\safety-lint.ps1 -Path C:\Projects\app -FailOn Medium
#>
[CmdletBinding()]
param(
  [string]$Path = 'C:\Alfred',
  [ValidateSet('High','Medium','Low')][string]$FailOn = 'High'
)
$ErrorActionPreference = 'Continue'

# Severity rank for gating.
$rank = @{ 'Low' = 1; 'Medium' = 2; 'High' = 3 }

# name | severity | regex (uses \s+ for real whitespace so denylist configs don't match)
$rules = @(
  [pscustomobject]@{ Name='Recursive/force rm'; Severity='High';   Rx='\brm\s+-[a-zA-Z]*[rf]' }
  [pscustomobject]@{ Name='Recursive Remove-Item'; Severity='High'; Rx='Remove-Item\b.*-Recurse' }
  [pscustomobject]@{ Name='Destructive git reset'; Severity='High'; Rx='git\s+reset\s+--hard' }
  [pscustomobject]@{ Name='git clean -f';        Severity='High';   Rx='git\s+clean\s+-[a-zA-Z]*f' }
  [pscustomobject]@{ Name='git force-push';      Severity='High';   Rx='git\s+push\b.*(--force|--force-with-lease|\s-f\b)' }
  [pscustomobject]@{ Name='Disk format';         Severity='High';   Rx='\bformat\s+[A-Za-z]:' }
  [pscustomobject]@{ Name='Download-and-execute'; Severity='High';  Rx='(Invoke-WebRequest|iwr|curl|wget|DownloadString)\b.*\|\s*(iex|Invoke-Expression|bash|sh)\b' }
  [pscustomobject]@{ Name='Invoke-Expression';   Severity='Medium'; Rx='\b(Invoke-Expression|iex)\b' }
  [pscustomobject]@{ Name='Registry write';      Severity='Medium'; Rx='\breg\s+(add|delete)\b|(Set|New|Remove)-ItemProperty\b.*HK(LM|CU|CR|U)' }
  [pscustomobject]@{ Name='Power/shutdown';      Severity='Medium'; Rx='\b(shutdown|Stop-Computer|Restart-Computer)\b' }
  [pscustomobject]@{ Name='Push to main/master'; Severity='Medium'; Rx='git\s+push\b.*\b(main|master)\b' }
  [pscustomobject]@{ Name='Auto-approve all tools'; Severity='Low'; Rx='--trust-all-tools' }
)

$exts = @('.ps1','.py','.sh','.js','.bat','.cmd')
$skipDirs = @('.git','node_modules','dist','build','out','.cache','target','venv','.venv','__pycache__','.wrangler')
$root = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
if (-not $root) { Write-Error "Path not found: $Path"; exit 1 }
Write-Host "[safety-lint] scanning $root (read-only; enforces safety.md)" -ForegroundColor Cyan

$self = 'safety-lint.ps1'
$files = Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  ($exts -contains $_.Extension.ToLower()) -and
  ($_.Name -ne $self) -and
  ((($_.FullName -split '[\\/]') | Where-Object { $skipDirs -contains $_ }).Count -eq 0)
}

$findings = @()
foreach ($f in $files) {
  $n = 0
  try { $lines = Get-Content -LiteralPath $f.FullName -ErrorAction Stop } catch { continue }
  foreach ($line in $lines) {
    $n++
    if ($line -match 'safety-lint:\s*allow') { continue }
    foreach ($rule in $rules) {
      if ($line -match $rule.Rx) {
        # Whitelist reviewed-safe recursive deletes (dry-run / confirmation present).
        if ($rule.Name -eq 'Recursive Remove-Item' -and $line -match '-WhatIf|-Confirm') { continue }
        $rel = $f.FullName.Substring($root.Path.Length).TrimStart('\','/')
        $findings += [pscustomobject]@{
          Severity = $rule.Severity
          Rule     = $rule.Name
          Location = ("{0}:{1}" -f $rel, $n)
          Snippet  = $line.Trim()
        }
      }
    }
  }
}

if ($findings.Count -gt 0) {
  $findings |
    Sort-Object @{ E = { $rank[$_.Severity] }; Descending = $true }, Location |
    Format-Table Severity, Rule, Location -AutoSize | Out-String | Write-Output
}

$hi  = @($findings | Where-Object { $_.Severity -eq 'High' }).Count
$med = @($findings | Where-Object { $_.Severity -eq 'Medium' }).Count
$low = @($findings | Where-Object { $_.Severity -eq 'Low' }).Count
$color = if ($hi) { 'Red' } elseif ($med) { 'Yellow' } else { 'Green' }
Write-Host ("[safety-lint] {0} file(s) scanned - High:{1} Medium:{2} Low:{3}" -f $files.Count, $hi, $med, $low) -ForegroundColor $color

$fail = @($findings | Where-Object { $rank[$_.Severity] -ge $rank[$FailOn] }).Count
if ($fail -gt 0) { Write-Host "[safety-lint] FAIL - $fail finding(s) at or above $FailOn." -ForegroundColor Red; exit 1 }
Write-Host "[safety-lint] PASS - nothing at or above $FailOn." -ForegroundColor Green
exit 0
