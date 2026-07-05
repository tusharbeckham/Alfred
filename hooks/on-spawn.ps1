# Alfred reflex: agentSpawn — log session start to the audit trail.
# Receives the hook event JSON on STDIN. Must never crash a session (always exit 0).
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $sid = if ($env:KIRO_SESSION_ID) { $env:KIRO_SESSION_ID } else { 'unknown' }
    $cwd = if ($evt -and $evt.cwd) { $evt.cwd } else { (Get-Location).Path }
    $line = "[$ts] SPAWN  session=$sid  cwd=$cwd"
    Add-Content -Path 'C:\Alfred\memory\session-log.txt' -Value $line -Encoding utf8
} catch {}
exit 0
