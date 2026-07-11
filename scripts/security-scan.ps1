<#
.SYNOPSIS
  Alfred security tool - scan a folder for likely-exposed secrets (API keys, tokens, private keys,
  passwords). DEFENSIVE and read-only: it reports findings with the secret REDACTED so you can rotate
  and remove them before they leak. A lightweight gitleaks/truffleHog for your OWN repos.
.DESCRIPTION
  Scans text files under -Path for common secret patterns and prints file:line + a masked snippet.
  It never prints full secret values, never modifies anything, and never transmits data anywhere.
  Point it at code you own.
.PARAMETER Path   Root folder to scan. Default: current directory.
.PARAMETER MaxKB  Skip files larger than this many KB (avoids binaries/blobs). Default 512.
.EXAMPLE  powershell -File scripts\security-scan.ps1 -Path C:\Projects\myapp
#>
[CmdletBinding()]
param(
  [string]$Path = ".",
  [int]$MaxKB = 512
)
$ErrorActionPreference = 'Continue'

# name => regex (conservative, to limit false positives)
$patterns = [ordered]@{
  'AWS Access Key ID'      = 'AKIA[0-9A-Z]{16}'
  'Private key block'      = '-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----'
  'Google API key'         = 'AIza[0-9A-Za-z\-_]{35}'
  'Slack token'            = 'xox[baprs]-[0-9A-Za-z-]{10,}'
  'Slack webhook'          = 'https://hooks\.slack\.com/services/[A-Za-z0-9/]+'
  'GitHub token'           = 'gh[pousr]_[0-9A-Za-z]{36,}'
  'JWT'                    = 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  'Generic API key/secret' = '(?i)(api[_-]?key|secret|token|passwd|password)\s*[=:]\s*["''][^"''\s]{8,}["'']'
}

$skipDirs = @('.git','node_modules','dist','build','out','.cache','target','venv','.venv','__pycache__')
$skipExt  = @('.png','.jpg','.jpeg','.gif','.ico','.pdf','.zip','.gz','.7z','.exe','.dll','.onnx','.bin','.wav','.mp3','.mp4','.woff','.woff2','.ttf')

function Mask([string]$s) {
  $s = $s.Trim()
  if ($s.Length -le 12) { return ('*' * $s.Length) }
  return $s.Substring(0,4) + ('*' * ($s.Length - 8)) + $s.Substring($s.Length - 4)
}

$root = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
if (-not $root) { Write-Error "Path not found: $Path"; exit 1 }
Write-Host "[security-scan] scanning $root  (read-only; secrets redacted)" -ForegroundColor Cyan

$files = Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  $_.Length -le ($MaxKB * 1KB) -and
  ($skipExt -notcontains $_.Extension.ToLower()) -and
  ((($_.FullName -split '[\\/]') | Where-Object { $skipDirs -contains $_ }).Count -eq 0)
}

$findings = 0
foreach ($f in $files) {
  $lineNo = 0
  try { $content = Get-Content -LiteralPath $f.FullName -ErrorAction Stop } catch { continue }
  foreach ($line in $content) {
    $lineNo++
    foreach ($name in $patterns.Keys) {
      $m = [regex]::Match($line, $patterns[$name])
      if ($m.Success) {
        $findings++
        $rel = $f.FullName.Substring($root.Path.Length).TrimStart('\', '/')
        Write-Host ("  [{0}] {1}:{2}  {3}" -f $name, $rel, $lineNo, (Mask $m.Value)) -ForegroundColor Yellow
      }
    }
  }
}

$color = if ($findings -gt 0) { 'Red' } else { 'Green' }
Write-Host ("[security-scan] done - {0} potential secret(s) across {1} files scanned." -f $findings, $files.Count) -ForegroundColor $color
if ($findings) { Write-Host "[security-scan] Rotate + remove each real secret, and make sure it's gitignored." -ForegroundColor DarkGray }
exit 0
