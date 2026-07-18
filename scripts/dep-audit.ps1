<#
.SYNOPSIS
  Alfred dep-audit - dependency + repo-hygiene audit. Read-only; installs nothing.
.DESCRIPTION
  Complements security-scan.ps1 (secret content) and safety-lint.ps1 (dangerous commands) by
  checking supply-chain and hygiene:
    * Unpinned dependencies in requirements.txt / package.json (floating versions are a
      supply-chain risk; safety.md says pin exact versions).
    * Secret-like files that are actually tracked by git (.env, *.pem, *.key, secrets/...).
    * .gitignore coverage for common secret patterns.
  Prints findings with severity and exits 1 if any High finding is present (tracked secrets).
.PARAMETER Path  Repo root to audit. Default: C:\Alfred
.EXAMPLE  powershell -File scripts\dep-audit.ps1
.EXAMPLE  powershell -File scripts\dep-audit.ps1 -Path C:\Projects\app
#>
[CmdletBinding()]
param([string]$Path = 'C:\Alfred')
$ErrorActionPreference = 'Continue'
$root = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
if (-not $root) { Write-Error "Path not found: $Path"; exit 1 }
Set-Location $root
Write-Host "[dep-audit] auditing $root (read-only)" -ForegroundColor Cyan

$findings = @()
function Add-Finding($sev, $kind, $detail) {
  $script:findings += [pscustomobject]@{ Severity = $sev; Kind = $kind; Detail = $detail }
}

# -- 1. Unpinned Python deps (requirements*.txt) --------------------------------------
Get-ChildItem -Recurse -File -Filter 'requirements*.txt' -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch '\\(node_modules|\.git|venv|\.venv)\\' } | ForEach-Object {
  $rel = $_.FullName.Substring($root.Path.Length).TrimStart('\','/')
  Get-Content -LiteralPath $_.FullName | ForEach-Object {
    $l = ($_ -split '\s+#')[0].Trim()
    if (-not $l -or $l.StartsWith('#') -or $l.StartsWith('-')) { return }
    if ($l -match '==\s*[0-9]' -or $l -match '@\s*[0-9a-f]{7,}') { return }  # pinned exact / commit
    if ($l -match '(>=|<=|~=|!=|>|<|\*|\^|~)') { Add-Finding 'Medium' 'Unpinned dep' "$rel -> $l (range)" }
    else { Add-Finding 'Medium' 'Unpinned dep' "$rel -> $l (no version)" }
  }
}

# -- 2. Unpinned Node deps (package.json) ---------------------------------------------
Get-ChildItem -Recurse -File -Filter 'package.json' -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch '\\(node_modules|\.git)\\' } | ForEach-Object {
  $rel = $_.FullName.Substring($root.Path.Length).TrimStart('\','/')
  try { $pkg = Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json } catch { return }
  foreach ($sec in 'dependencies','devDependencies') {
    if (-not $pkg.$sec) { continue }
    foreach ($p in $pkg.$sec.PSObject.Properties) {
      $v = [string]$p.Value
      if ($v -match '^[=]?[0-9]' -and $v -notmatch '[\^~x\*]|\s-\s') { continue }   # exact
      if ($v -match '^(latest|\*|x)$' -or $v -match '[\^~]|\bx\b|>=|<=|>|<|\|\|') {
        Add-Finding 'Medium' 'Unpinned dep' "$rel -> $($p.Name): $v"
      }
    }
  }
}

# -- 3. Secret-like files tracked by git ----------------------------------------------
# Flags real secret files; NOT the .env.example/.sample/.template convention (meant to be committed).
$secretRx = '(^|/)(\.env(\.(local|prod|production|dev|staging|secret))?|.*\.pem|.*\.key|.*\.pfx|.*\.p12|.*\.keystore|id_rsa.*|.*\.ppk)$'
try {
  $tracked = git ls-files 2>$null
  foreach ($t in $tracked) {
    if ($t -match $secretRx -or $t -match '(^|/)secrets/') {
      Add-Finding 'High' 'Tracked secret file' $t
    }
  }
} catch { Add-Finding 'Low' 'git' 'could not run git ls-files (not a repo?)' }

# -- 4. .gitignore coverage -----------------------------------------------------------
$giPath = Join-Path $root '.gitignore'
if (Test-Path $giPath) {
  $gi = Get-Content -Raw -LiteralPath $giPath
  foreach ($pat in '.env','secrets/','*.key','*.pem') {
    $escaped = [regex]::Escape($pat)
    if ($gi -notmatch $escaped) { Add-Finding 'Low' 'gitignore gap' "no rule covering '$pat'" }
  }
} else { Add-Finding 'Medium' 'gitignore' 'no .gitignore present' }

# -- Report ---------------------------------------------------------------------------
if ($findings.Count -gt 0) {
  $rank = @{ 'Low'=1; 'Medium'=2; 'High'=3 }
  $findings | Sort-Object @{E={$rank[$_.Severity]};Descending=$true}, Kind |
    Format-Table Severity, Kind, Detail -AutoSize -Wrap | Out-String | Write-Output
}
$hi  = @($findings | Where-Object { $_.Severity -eq 'High' }).Count
$med = @($findings | Where-Object { $_.Severity -eq 'Medium' }).Count
$low = @($findings | Where-Object { $_.Severity -eq 'Low' }).Count
$color = if ($hi) { 'Red' } elseif ($med) { 'Yellow' } else { 'Green' }
Write-Host ("[dep-audit] done - High:{0} Medium:{1} Low:{2}" -f $hi, $med, $low) -ForegroundColor $color
if ($hi -gt 0) { exit 1 }
exit 0
