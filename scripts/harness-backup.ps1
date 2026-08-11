<#
.SYNOPSIS
  Gated harness capability: back up Alfred's configuration tree.
.DESCRIPTION
  Copies the config/prompt/policy surface to a timestamped folder under backups/.
  Never touches secrets, never deletes anything, never leaves the repo.
  Invoked only through scripts/harness.py (capability "backup", gated, high-trust callers).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$dest = Join-Path $root "backups\$stamp"

$include = @('.kiro', 'hooks', 'workflows', 'policy', 'prompts', 'evals', 'AGENTS.md', 'README.md')

New-Item -ItemType Directory -Path $dest -Force | Out-Null
$copied = 0
foreach ($item in $include) {
    $source = Join-Path $root $item
    if (-not (Test-Path $source)) { continue }
    Copy-Item -Path $source -Destination $dest -Recurse -Force -ErrorAction Stop
    $copied++
}

# Defence in depth: the backup must never contain key material.
$leaked = Get-ChildItem -Path $dest -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '\.(key|pem|pfx)$' -or $_.Name -eq '.env' -or $_.FullName -match '\\secrets\\' }
if ($leaked) {
    $leaked | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Warning "Removed $($leaked.Count) sensitive file(s) from the backup."
}

$size = (Get-ChildItem -Path $dest -Recurse -File | Measure-Object -Property Length -Sum).Sum
[pscustomobject]@{
    backup    = $dest
    trees     = $copied
    files     = (Get-ChildItem -Path $dest -Recurse -File).Count
    sizeMB    = [math]::Round($size / 1MB, 2)
} | ConvertTo-Json

exit 0
