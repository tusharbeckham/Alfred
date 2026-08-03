<#
.SYNOPSIS
  Alfred install-git-hooks - install (or remove) the pre-commit secret scan and the
  pre-push branch guard.
.DESCRIPTION
  Wires two git hooks:
    * pre-commit -> scripts/scan-staged.ps1   (every commit is checked for secrets)
    * pre-push   -> scripts/protect-main.ps1  (refuses direct pushes to main/master,
                                               force pushes, and branch deletions)
  The pre-push guard exists because GitHub's server-side branch protection and rulesets
  both require GitHub Pro or a public repo - on a private free repo they return HTTP 403 -
  so the rules are enforced client-side instead.

  Any pre-existing, non-Alfred hook is backed up to '<hook>.pre-alfred' rather than
  overwritten. Use -Uninstall to remove ours (and restore a backup if present). Either
  hook can be bypassed for a single command with --no-verify.
.PARAMETER Uninstall  Remove the Alfred hooks (restoring any backups).
.EXAMPLE  powershell -File scripts\install-git-hooks.ps1
.EXAMPLE  powershell -File scripts\install-git-hooks.ps1 -Uninstall
#>
[CmdletBinding()]
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$hooksDir = (git rev-parse --git-path hooks 2>$null)
if (-not $hooksDir) { Write-Error "Not a git repository."; exit 1 }
if (-not (Test-Path $hooksDir)) { New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null }
$marker = 'ALFRED-HOOK'

# name | the sh body that runs our script | the message shown when it blocks
$hookSpecs = @(
  [pscustomobject]@{
    Name    = 'pre-commit'
    Comment = 'Scans staged changes for secrets before the commit lands. Bypass once with --no-verify.'
    Script  = 'scripts/scan-staged.ps1'
    Blocked = '[pre-commit] Alfred staged-scan blocked this commit (see above).'
    Stdin   = $false
  }
  [pscustomobject]@{
    Name    = 'pre-push'
    Comment = 'Refuses direct pushes to main/master, force pushes and branch deletions.'
    Script  = 'scripts/protect-main.ps1'
    Blocked = '[pre-push] Alfred branch guard blocked this push (see above).'
    Stdin   = $true
  }
)

if ($Uninstall) {
  $removed = 0
  foreach ($spec in $hookSpecs) {
    $hook = Join-Path $hooksDir $spec.Name
    $backup = "$hook.pre-alfred"
    if ((Test-Path $hook) -and ((Get-Content -Raw $hook) -match $marker)) {
      Remove-Item $hook -Force
      if (Test-Path $backup) { Move-Item $backup $hook -Force; Write-Host "[hooks] removed Alfred $($spec.Name); restored previous hook." -ForegroundColor Green }
      else { Write-Host "[hooks] removed Alfred $($spec.Name) hook." -ForegroundColor Green }
      $removed++
    }
  }
  if ($removed -eq 0) { Write-Host "[hooks] no Alfred hooks installed; nothing to do." -ForegroundColor DarkGray }
  exit 0
}

foreach ($spec in $hookSpecs) {
  $hook = Join-Path $hooksDir $spec.Name
  $backup = "$hook.pre-alfred"

  # Back up a foreign existing hook so we never clobber the Owner's own hook.
  if ((Test-Path $hook) -and ((Get-Content -Raw $hook) -notmatch $marker)) {
    Copy-Item $hook $backup -Force
    Write-Host "[hooks] backed up existing $($spec.Name) -> $($spec.Name).pre-alfred" -ForegroundColor Yellow
  }

  # POSIX sh script (git runs hooks via sh, including on Windows). LF endings, no BOM.
  # pre-push must forward git's ref payload on STDIN to the guard.
  $invoke = "powershell -NoProfile -ExecutionPolicy Bypass -File $($spec.Script)"
  $body = @(
    '#!/bin/sh',
    "# $marker - installed by scripts/install-git-hooks.ps1",
    "# $($spec.Comment)",
    $invoke,
    'code=$?',
    'if [ "$code" -ne 0 ]; then',
    "  echo `"$($spec.Blocked)`"",
    '  exit "$code"',
    'fi',
    'exit 0'
  ) -join "`n"
  [System.IO.File]::WriteAllText($hook, $body + "`n")
  Write-Host "[hooks] installed $($spec.Name) -> $hook" -ForegroundColor Green
}

Write-Host "[hooks] pre-commit runs scripts/scan-staged.ps1; pre-push runs scripts/protect-main.ps1." -ForegroundColor DarkGray
Write-Host "[hooks] main/master are now push-protected locally. Override: `$env:ALFRED_ALLOW_PUSH='main'" -ForegroundColor DarkGray
exit 0
