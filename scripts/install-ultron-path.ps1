<#
.SYNOPSIS
  Wire `ultron` onto the user PATH so it runs from any folder (like kiro-cli).
.DESCRIPTION
  Adds the Alfred repo root (which holds ultron.cmd / ultron.ps1) to the CURRENT USER's
  PATH via the .NET environment API - user scope only, no truncation, fully reversible.
  Idempotent: running it twice does nothing the second time. Use -Uninstall to remove.
  A NEW terminal is required for the change to take effect (env vars are read at shell start).
.EXAMPLE  powershell -File scripts\install-ultron-path.ps1
.EXAMPLE  powershell -File scripts\install-ultron-path.ps1 -Uninstall
#>
[CmdletBinding()]
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot           # scripts\ -> repo root
if (-not (Test-Path (Join-Path $root 'ultron.cmd'))) {
    Write-Error "ultron.cmd not found in $root - run this from the Alfred repo."; exit 1
}

$scope = 'User'
$cur = [Environment]::GetEnvironmentVariable('Path', $scope)
$parts = @()
if ($cur) { $parts = @($cur -split ';' | Where-Object { $_ -ne '' }) }
$normRoot = $root.TrimEnd('\')
$present = @($parts | Where-Object { $_.TrimEnd('\') -ieq $normRoot }).Count -gt 0

if ($Uninstall) {
    if ($present) {
        $new = ($parts | Where-Object { $_.TrimEnd('\') -ine $normRoot }) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $new, $scope)
        Write-Host "[ultron] removed from user PATH: $root" -ForegroundColor Green
        Write-Host "[ultron] open a NEW terminal for it to take effect." -ForegroundColor DarkGray
    } else {
        Write-Host "[ultron] $root is not on user PATH; nothing to remove." -ForegroundColor DarkGray
    }
    exit 0
}

if ($present) {
    Write-Host "[ultron] already on user PATH: $root" -ForegroundColor DarkGray
} else {
    $new = (@($parts) + $root) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $new, $scope)
    Write-Host "[ultron] added to user PATH: $root" -ForegroundColor Green
}
Write-Host "[ultron] open a NEW terminal, then run:  ultron doctor" -ForegroundColor Cyan
Write-Host "[ultron] to undo:  powershell -File scripts\install-ultron-path.ps1 -Uninstall" -ForegroundColor DarkGray
exit 0
