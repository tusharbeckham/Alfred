<#
.SYNOPSIS
  Alfred install-git-hooks - install (or remove) a pre-commit hook that runs the staged secret scan.
.DESCRIPTION
  Wires scripts/scan-staged.ps1 into .git/hooks/pre-commit so every commit is checked for secrets
  before it lands. Any pre-existing, non-Alfred pre-commit hook is backed up to
  'pre-commit.pre-alfred' rather than overwritten. Use -Uninstall to remove ours (and restore a
  backup if present). The hook can always be bypassed for a single commit with `git commit --no-verify`.
.PARAMETER Uninstall  Remove the Alfred hook (restoring any backup).
.EXAMPLE  powershell -File scripts\install-git-hooks.ps1
.EXAMPLE  powershell -File scripts\install-git-hooks.ps1 -Uninstall
#>
[CmdletBinding()]
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$hooksDir = (git rev-parse --git-path hooks 2>$null)
if (-not $hooksDir) { Write-Error "Not a git repository."; exit 1 }
if (-not (Test-Path $hooksDir)) { New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null }
$hook = Join-Path $hooksDir 'pre-commit'
$backup = Join-Path $hooksDir 'pre-commit.pre-alfred'
$marker = 'ALFRED-HOOK'

if ($Uninstall) {
  if ((Test-Path $hook) -and ((Get-Content -Raw $hook) -match $marker)) {
    Remove-Item $hook -Force
    if (Test-Path $backup) { Move-Item $backup $hook -Force; Write-Host "[hooks] removed Alfred hook; restored previous pre-commit." -ForegroundColor Green }
    else { Write-Host "[hooks] removed Alfred pre-commit hook." -ForegroundColor Green }
  } else {
    Write-Host "[hooks] no Alfred pre-commit hook installed; nothing to do." -ForegroundColor DarkGray
  }
  exit 0
}

# Back up a foreign existing hook so we never clobber the Owner's own hook.
if ((Test-Path $hook) -and ((Get-Content -Raw $hook) -notmatch $marker)) {
  Copy-Item $hook $backup -Force
  Write-Host "[hooks] backed up existing pre-commit -> pre-commit.pre-alfred" -ForegroundColor Yellow
}

# POSIX sh script (git runs hooks via sh, including on Windows). LF endings, no BOM.
$body = @(
  '#!/bin/sh',
  "# $marker - installed by scripts/install-git-hooks.ps1",
  '# Scans staged changes for secrets before the commit lands. Bypass once with --no-verify.',
  'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/scan-staged.ps1',
  'code=$?',
  'if [ "$code" -ne 0 ]; then',
  '  echo "[pre-commit] Alfred staged-scan blocked this commit (see above)."',
  '  exit "$code"',
  'fi',
  'exit 0'
) -join "`n"
[System.IO.File]::WriteAllText($hook, $body + "`n")

Write-Host "[hooks] installed pre-commit -> $hook" -ForegroundColor Green
Write-Host "[hooks] it runs scripts/scan-staged.ps1 on every commit. Test: git add . ; git commit ..." -ForegroundColor DarkGray
exit 0
