# Alfred reflex: preToolUse(shell) CI GATE — block `git commit`/`git push` when the last
# CI run failed. Exit 2 blocks the tool and returns STDERR to the agent; exit 0 allows.
# Wire optionally into devops via:
#   "preToolUse": [ { "matcher": "shell", "command": "powershell -NoProfile -ExecutionPolicy Bypass -File hooks/ci-gate.ps1" } ]
try {
    $raw = [Console]::In.ReadToEnd()
    $evt = $null
    if ($raw) { try { $evt = $raw | ConvertFrom-Json } catch {} }
    $cmd = ''
    if ($evt -and $evt.tool_input -and $evt.tool_input.command) { $cmd = $evt.tool_input.command }

    if ($cmd -match 'git\s+(commit|push)') {
        $ci = 'C:\Alfred\memory\ci-results.md'
        if (Test-Path $ci) {
            $last = Get-Content $ci | Where-Object { $_ -match 'CI:\s*(PASS|FAIL)' } | Select-Object -Last 1
            if ($last -match 'CI:\s*FAIL') {
                [Console]::Error.WriteLine('CI GATE: the last CI run FAILED. Run scripts/ci-run.ps1 and make it PASS before committing/pushing.')
                exit 2
            }
        }
    }
} catch {}
exit 0
