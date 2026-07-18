<#
.SYNOPSIS
  Alfred scan-staged - scan git STAGED changes for secrets before they are committed.
.DESCRIPTION
  A git-secrets-style gate: it inspects only what you are about to commit (the added lines in
  `git diff --cached`), so it flags NEW secrets without drowning in pre-existing noise. It also
  flags secret-like files being added (.env, *.pem, *.key, secrets/...). Read-only; secrets are
  REDACTED in output; nothing is transmitted. Exit 1 if anything is found (so a pre-commit hook
  can block the commit); exit 0 when clean.
.PARAMETER All  Scan the full staged content of each file, not just the added lines.
.EXAMPLE  powershell -File scripts\scan-staged.ps1
.EXAMPLE  git add . ; powershell -File scripts\scan-staged.ps1   # what a pre-commit hook runs
#>
[CmdletBinding()]
param([switch]$All)
$ErrorActionPreference = 'Continue'
Set-Location (git rev-parse --show-toplevel 2>$null)

$patterns = [ordered]@{
  'AWS Access Key ID'      = 'AKIA[0-9A-Z]{16}'
  'Private key block'      = '-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----'
  'Google API key'         = 'AIza[0-9A-Za-z\-_]{35}'
  'Slack token'            = 'xox[baprs]-[0-9A-Za-z-]{10,}'
  'GitHub token'           = 'gh[pousr]_[0-9A-Za-z]{36,}'
  'JWT'                    = 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
  'Generic API key/secret' = '(?i)(api[_-]?key|secret|token|passwd|password)\s*[=:]\s*["''][^"''\s]{8,}["'']'
}
$secretFileRx = '(^|/)(\.env(\.(local|prod|production|dev|staging|secret))?|.*\.pem|.*\.key|.*\.pfx|.*\.p12|.*\.keystore|id_rsa.*|.*\.ppk)$'

function Mask([string]$s) {
  $s = $s.Trim()
  if ($s.Length -le 12) { return ('*' * $s.Length) }
  return $s.Substring(0,4) + ('*' * ($s.Length - 8)) + $s.Substring($s.Length - 4)
}

$findings = 0

# 1. Secret-like files being added/modified.
$stagedFiles = @(git diff --cached --name-only --diff-filter=ACM 2>$null)
foreach ($f in $stagedFiles) {
  if ($f -match $secretFileRx -or $f -match '(^|/)secrets/') {
    $findings++
    Write-Host ("  [Secret-like file staged] {0}" -f $f) -ForegroundColor Red
  }
}

# 2. Added lines (or full staged content with -All).
$diffArgs = if ($All) { @('diff','--cached','--no-color') } else { @('diff','--cached','--unified=0','--no-color') }
$diff = git @diffArgs 2>$null
$curFile = ''
foreach ($line in $diff) {
  if ($line -match '^\+\+\+ b/(.*)') { $curFile = $Matches[1]; continue }
  if ($line -notmatch '^\+' -or $line -match '^\+\+\+') { continue }
  $content = $line.Substring(1)
  foreach ($name in $patterns.Keys) {
    $m = [regex]::Match($content, $patterns[$name])
    if ($m.Success) {
      $findings++
      Write-Host ("  [{0}] {1}  {2}" -f $name, $curFile, (Mask $m.Value)) -ForegroundColor Yellow
    }
  }
}

if ($findings -gt 0) {
  Write-Host ("[scan-staged] BLOCKED - {0} potential secret finding(s) in staged changes." -f $findings) -ForegroundColor Red
  Write-Host "[scan-staged] Remove/rotate the secret and unstage it, or re-run with git commit --no-verify to override." -ForegroundColor DarkGray
  exit 1
}
Write-Host "[scan-staged] clean - no secrets in staged changes." -ForegroundColor Green
exit 0
