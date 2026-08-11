<#
.SYNOPSIS
  Gated harness capability: commit already-staged changes. NEVER pushes.
.DESCRIPTION
  Deliberately refuses to stage anything itself — the Owner (or an agent under review)
  decides what goes in the commit with `git add <specific files>`. This script only
  records what is already staged, after refusing any staged file that looks like a secret.

  Invoked only through scripts/harness.py (capability "git-commit", gated, high trust).
  It cannot push: no push logic exists here, and `git push` is in the policy's forbidden
  argument patterns.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Message
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Message)) { Write-Error 'A commit message is required.'; exit 4 }

$staged = @(git diff --cached --name-only 2>$null | Where-Object { $_ })
if ($staged.Count -eq 0) {
    Write-Error 'Nothing is staged. Stage specific files first (never `git add .`), then retry.'
    exit 4
}

$suspicious = $staged | Where-Object {
    $_ -match '(?i)(^|/)\.env($|\.)' -or $_ -match '(?i)/secrets/' -or
    $_ -match '(?i)\.(key|pem|pfx)$' -or $_ -match '(?i)id_rsa' -or
    $_ -match '(?i)harness-policy\.(json|sig)$' -or $_ -match '(?i)credentials'
}
if ($suspicious) {
    Write-Error ("Refusing to commit: these staged paths may contain secrets or protected policy:`n  " + ($suspicious -join "`n  "))
    exit 3
}

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -in @('main', 'master')) {
    Write-Error "Refusing to commit directly on '$branch'. Create a branch first."
    exit 3
}

git commit -m $Message | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error 'git commit failed.'; exit 5 }

[pscustomobject]@{
    committed = $true
    branch    = $branch
    sha       = (git rev-parse --short HEAD).Trim()
    files     = $staged.Count
    pushed    = $false
    note      = 'Nothing was pushed. Pushing is out of scope for the harness by design.'
} | ConvertTo-Json

exit 0
