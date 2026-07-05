# Alfred reflex: preToolUse(write) — capture pending git changes before a write, for audit.
# Exit 0 = allow the write. (Exit 2 would block; Alfred does not block writes here.)
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $cwd = if ($evt -and $evt.cwd) { $evt.cwd } else { (Get-Location).Path }
    $path = ''
    if ($evt -and $evt.tool_input -and $evt.tool_input.path) { $path = $evt.tool_input.path }
    $status = ''
    try { $status = ((git -C $cwd status --short 2>$null) -join '; ') } catch {}
    $line = "[$ts] PRE-WRITE  target=$path  pending=[$status]"
    Add-Content -Path 'C:\Alfred\memory\shell-log.txt' -Value $line -Encoding utf8
} catch {}
exit 0
