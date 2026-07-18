<#
.SYNOPSIS
  Alfred menu - a discoverable command palette of every Alfred script + workflow. Read-only.
.DESCRIPTION
  Lists the automation surface with one-line descriptions pulled straight from each script's
  own .SYNOPSIS (or leading comment), so it never goes stale. New scripts show up automatically.
  Use -Filter to narrow by keyword.
.PARAMETER Filter  Case-insensitive substring to match against name or description.
.EXAMPLE  powershell -File scripts\alfred-menu.ps1
.EXAMPLE  powershell -File scripts\alfred-menu.ps1 -Filter security
#>
[CmdletBinding()]
param([string]$Filter = '')
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'

function Get-Synopsis($file) {
  $lines = Get-Content -LiteralPath $file -ErrorAction SilentlyContinue
  $idx = ($lines | Select-String -SimpleMatch '.SYNOPSIS' | Select-Object -First 1).LineNumber
  if ($idx) {
    for ($i = $idx; $i -lt $lines.Count; $i++) {
      $t = $lines[$i].Trim()
      if ($t) { return $t }
    }
  }
  # Fallback: first non-shebang comment line.
  foreach ($l in $lines) {
    $t = $l.Trim()
    if ($t -match '^#!' ) { continue }
    if ($t.StartsWith('#')) { return ($t.TrimStart('#',' ')) }
    if ($t -match '^"""') { return ($t.Trim('"',' ')) }
  }
  return '(no description)'
}

Write-Host "==================== ALFRED MENU ====================" -ForegroundColor White
$scripts = Get-ChildItem 'scripts' -Recurse -Include '*.ps1','*.py' -File -ErrorAction SilentlyContinue |
  Sort-Object Name
foreach ($s in $scripts) {
  $desc = Get-Synopsis $s.FullName
  $rel = $s.FullName.Substring((Get-Location).Path.Length).TrimStart('\','/')
  if ($Filter -and ($rel -notmatch [regex]::Escape($Filter)) -and ($desc -notmatch [regex]::Escape($Filter))) { continue }
  $name = $s.Name
  Write-Host ("  {0,-26}" -f $name) -ForegroundColor Green -NoNewline
  Write-Host (" {0}" -f $desc) -ForegroundColor Gray
}

Write-Host "`n-- Workflows (scripts\workflow-run.ps1 -Workflow <name>) --" -ForegroundColor Cyan
Get-ChildItem 'workflows' -Filter '*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
  try { $d = (Get-Content -Raw $_.FullName | ConvertFrom-Json).description } catch { $d = '' }
  $nm = $_.BaseName
  if ($Filter -and ($nm -notmatch [regex]::Escape($Filter)) -and ($d -notmatch [regex]::Escape($Filter))) { return }
  Write-Host ("  {0,-26}" -f $nm) -ForegroundColor Green -NoNewline
  Write-Host (" {0}" -f $d) -ForegroundColor Gray
}
Write-Host "====================================================" -ForegroundColor White
