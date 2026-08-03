# Alfred reflex: agentSpawn / SessionStart - log session start to the audit trail.
# Receives the hook event JSON on STDIN. Must never crash a session (always exit 0).
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $sid = 'unknown'
    if ($evt -and $evt.session_id)  { $sid = $evt.session_id }
    elseif ($env:CLAUDE_SESSION_ID) { $sid = $env:CLAUDE_SESSION_ID }
    elseif ($env:KIRO_SESSION_ID)   { $sid = $env:KIRO_SESSION_ID }
    $cwd = if ($evt -and $evt.cwd) { $evt.cwd } else { (Get-Location).Path }
    $line = "[$ts] SPAWN  session=$sid  cwd=$cwd"
    $root = if ($env:ALFRED_ROOT) { $env:ALFRED_ROOT } else { Split-Path -Parent $PSScriptRoot }
    $log  = Join-Path $root 'memory\session-log.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Add-Content -Path $log -Value $line -Encoding utf8
} catch {}
exit 0
