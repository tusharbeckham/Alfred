# Alfred reflex: preToolUse(write) - capture pending git changes before a write, for audit.
# Exit 0 = allow the write. (Exit 2 would block; Alfred does not block writes here.)
# Runs under both Kiro (tool_input.path) and Claude Code (tool_input.file_path).
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $ts  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $cwd = if ($evt -and $evt.cwd) { $evt.cwd } else { (Get-Location).Path }
    $path = ''
    if ($evt -and $evt.tool_input) {
        # Kiro sends .path; Claude Code's Write/Edit send .file_path.
        if ($evt.tool_input.file_path) { $path = $evt.tool_input.file_path }
        elseif ($evt.tool_input.path)  { $path = $evt.tool_input.path }
    }
    $status = ''
    try { $status = ((git -C $cwd status --short 2>$null) -join '; ') } catch {}
    $line = "[$ts] PRE-WRITE  target=$path  pending=[$status]"
    $root = if ($env:ALFRED_ROOT) { $env:ALFRED_ROOT } else { Split-Path -Parent $PSScriptRoot }
    $log  = Join-Path $root 'memory\shell-log.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Add-Content -Path $log -Value $line -Encoding utf8
} catch {}
exit 0
