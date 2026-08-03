# Alfred reflex: postToolUse(shell) - log every shell command and its exit status.
# Runs under both Kiro (shell tool) and Claude Code (Bash tool); both send tool_input.command.
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $cmd = ''
    if ($evt -and $evt.tool_input -and $evt.tool_input.command) { $cmd = $evt.tool_input.command }
    $code = ''
    if ($evt -and $evt.tool_response) {
        if ($null -ne $evt.tool_response.exit_status)     { $code = $evt.tool_response.exit_status }
        elseif ($null -ne $evt.tool_response.exit_code)   { $code = $evt.tool_response.exit_code }
        elseif ($null -ne $evt.tool_response.success)     { $code = $evt.tool_response.success }
    }
    $cmd1 = ($cmd -replace '\s+', ' ').Trim()
    if ($cmd1.Length -gt 160) { $cmd1 = $cmd1.Substring(0, 160) + '...' }
    $line = "[$ts] SHELL  exit=$code  cmd=$cmd1"
    $root = if ($env:ALFRED_ROOT) { $env:ALFRED_ROOT } else { Split-Path -Parent $PSScriptRoot }
    $log  = Join-Path $root 'memory\shell-log.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Add-Content -Path $log -Value $line -Encoding utf8
} catch {}
exit 0
