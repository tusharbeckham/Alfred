# Alfred — Overnight run. Headless, sandboxed, CI-gated. Schedule ~02:00 (see below).
# Works the memory/todo.md backlog, self-tests, commits clean work behind the CI gate, and
# records anything needing approval to the Approvals List. NEVER performs gated actions.
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$log = "C:\Alfred\memory\overnight-$stamp.log"
"[overnight] start $(Get-Date -Format o)" | Tee-Object -FilePath $log
$directive = "Execute the overnight run EXACTLY per prompts/overnight/overnight-run.txt. You " +
  "are unsupervised and SANDBOXED: project work only, no safety-gated actions (no deletes, no " +
  "system/registry changes, no installs, no prod, no push to main, no force ops). Commit only " +
  "behind a green CI gate (scripts/ci-run.ps1). Record anything needing approval to the " +
  "Approvals List in memory/todo.md and continue. Append a session summary to memory/decisions.md."
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-leader $directive 2>&1 |
  Tee-Object -FilePath $log -Append
"[overnight] end $(Get-Date -Format o) exit=$LASTEXITCODE" | Tee-Object -FilePath $log -Append

# --- Scheduling (run once, elevated) -------------------------------------------------
# $act = New-ScheduledTaskAction -Execute 'powershell' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Alfred\scripts\overnight-run.ps1'
# $trg = New-ScheduledTaskTrigger -Daily -At 2:00AM
# Register-ScheduledTask -TaskName 'Alfred-Overnight' -Action $act -Trigger $trg -Description 'Alfred sandboxed overnight run'
