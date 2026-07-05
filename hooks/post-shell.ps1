# Alfred reflex: postToolUse(shell) — log every shell command and its exit status.
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $cmd = ''
    if ($evt -and $evt.tool_input -and $evt.tool_input.command) { $cmd = $evt.tool_input.command }
    $code = ''
    if ($evt -and $evt.tool_response) {
        if ($null -ne $evt.tool_response.exit_status) { $code = $evt.tool_response.exit_status }
        elseif ($null -ne $evt.tool_response.success) { $code = $evt.tool_response.success }
    }
    $cmd1 = ($cmd -replace '\s+', ' ').Trim()
    if ($cmd1.Length -gt 160) { $cmd1 = $cmd1.Substring(0, 160) + '...' }
    $line = "[$ts] SHELL  exit=$code  cmd=$cmd1"
    Add-Content -Path 'C:\Alfred\memory\shell-log.txt' -Value $line -Encoding utf8
} catch {}
exit 0
