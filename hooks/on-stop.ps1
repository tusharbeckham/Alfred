# Alfred reflex: stop - log a session-end marker with a short response preview.
# Curated decisions/learnings are written by the agents themselves; this is the audit trail.
# Runs under both Kiro and Claude Code (Stop hook).
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $sid = 'unknown'
    if ($evt -and $evt.session_id)        { $sid = $evt.session_id }
    elseif ($env:CLAUDE_SESSION_ID)       { $sid = $env:CLAUDE_SESSION_ID }
    elseif ($env:KIRO_SESSION_ID)         { $sid = $env:KIRO_SESSION_ID }
    $resp = if ($evt -and $evt.assistant_response) { $evt.assistant_response } else { '' }
    $preview = ($resp -replace '\s+', ' ').Trim()
    if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) + '...' }
    $line = "[$ts] STOP   session=$sid  summary=$preview"
    $root = if ($env:ALFRED_ROOT) { $env:ALFRED_ROOT } else { Split-Path -Parent $PSScriptRoot }
    $log  = Join-Path $root 'memory\session-log.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Add-Content -Path $log -Value $line -Encoding utf8
} catch {}
exit 0
