# Alfred reflex: stop — log a session-end marker with a short response preview.
# Curated decisions/learnings are written by the agents themselves; this is the audit trail.
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $sid = if ($env:KIRO_SESSION_ID) { $env:KIRO_SESSION_ID } else { 'unknown' }
    $resp = if ($evt -and $evt.assistant_response) { $evt.assistant_response } else { '' }
    $preview = ($resp -replace '\s+', ' ').Trim()
    if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) + '...' }
    $line = "[$ts] STOP   session=$sid  summary=$preview"
    Add-Content -Path 'C:\Alfred\memory\session-log.txt' -Value $line -Encoding utf8
} catch {}
exit 0
