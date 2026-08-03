<#
.SYNOPSIS
  Alfred protect-main - a pre-push guard that refuses direct pushes to main, force
  pushes, and remote branch deletions.
.DESCRIPTION
  GitHub's server-side branch protection (and rulesets) require GitHub Pro or a public
  repository; on a private free repo both endpoints return HTTP 403. This enforces the
  same rules locally instead, which is where the real risk lives: an agent - or the Owner
  in a hurry - running the wrong push.

  Reads git's pre-push payload on STDIN, one line per ref:
      <local ref> <local sha> <remote ref> <remote sha>

  Blocks, with exit 1:
    1. Any push whose REMOTE ref is a protected branch (default: main, master).
    2. Any branch DELETION (local sha is all zeros).
    3. Any NON-FAST-FORWARD push, i.e. a force push that would discard commits
       (remote sha is not an ancestor of the local sha).

  Mirrors .kiro/steering/safety.md, which already forbids all three.

  ESCAPE HATCH - the Owner is never locked out. Either:
      $env:ALFRED_ALLOW_PUSH = 'main'     # allow this one protected push
      git push --no-verify ...            # skip the hook entirely
.PARAMETER ProtectedBranches
  Branch names that may not be pushed to directly. Default: main, master.
.EXAMPLE
  # Installed automatically as .git/hooks/pre-push by scripts\install-git-hooks.ps1
  powershell -File scripts\protect-main.ps1
#>
[CmdletBinding()]
param([string[]]$ProtectedBranches = @('main', 'master'))

$ErrorActionPreference = 'Stop'
$ZERO = '0{40}'   # git uses an all-zero sha to mean "no such commit"

# The override is deliberately explicit: it must name the branch, so a stale
# environment variable can't silently unprotect everything.
$override = $env:ALFRED_ALLOW_PUSH

$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }   # nothing to push (e.g. "Everything up-to-date")

$violations = @()

foreach ($line in ($raw -split "`n")) {
    $line = $line.Trim()
    if (-not $line) { continue }
    $parts = $line -split '\s+'
    if ($parts.Count -lt 4) { continue }
    $localRef, $localSha, $remoteRef, $remoteSha = $parts[0], $parts[1], $parts[2], $parts[3]

    $branch = $remoteRef -replace '^refs/heads/', ''

    # --- 1. deletion (checked before the protected-branch test so the message is precise)
    if ($localSha -match "^$ZERO$") {
        $violations += "DELETE of remote branch '$branch' - deleting a remote branch needs the Owner's explicit approval."
        continue
    }

    # --- 2. direct push to a protected branch
    if ($ProtectedBranches -contains $branch) {
        if ($override -eq $branch) {
            Write-Host "[protect-main] ALFRED_ALLOW_PUSH='$branch' - allowing this push to '$branch'." -ForegroundColor Yellow
        } else {
            $violations += "DIRECT PUSH to protected branch '$branch' - land it through a pull request instead."
            continue
        }
    }

    # --- 3. non-fast-forward (force) push: remote tip must be reachable from the new tip
    if ($remoteSha -notmatch "^$ZERO$") {
        git merge-base --is-ancestor $remoteSha $localSha 2>$null
        if ($LASTEXITCODE -ne 0) {
            $violations += "FORCE PUSH to '$branch' - this would discard commits already on the remote."
        }
    }
}

if ($violations.Count -eq 0) { exit 0 }

Write-Host ""
Write-Host "[protect-main] PUSH BLOCKED - $($violations.Count) violation(s):" -ForegroundColor Red
foreach ($v in $violations) { Write-Host "  - $v" -ForegroundColor Red }
Write-Host ""
Write-Host "  Alfred's safety rules (.kiro/steering/safety.md) forbid these without the Owner's approval." -ForegroundColor DarkGray
Write-Host "  If this is genuinely intended:" -ForegroundColor DarkGray
Write-Host "    `$env:ALFRED_ALLOW_PUSH = '<branch>'   # then re-run the push" -ForegroundColor DarkGray
Write-Host "    git push --no-verify ...              # or bypass the hook entirely" -ForegroundColor DarkGray
Write-Host ""
exit 1
