# Alfred — Morning report. Briefs the Owner from memory/. Schedule ~07:00.
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$stamp = Get-Date -Format 'yyyy-MM-dd'
$out = "C:\Alfred\memory\morning-report-$stamp.md"
$directive = "Produce the Owner's morning report per prompts/overnight/morning-report.txt from " +
  "the memory/ files (decisions, learnings, todo, ci-results, overnight logs). Write the report " +
  "to '$out' and also print it. Put the Approvals List front and center."
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-manager $directive
Write-Host "[morning-report] written to $out (exit=$LASTEXITCODE)"

# --- Scheduling (run once, elevated) -------------------------------------------------
# $act = New-ScheduledTaskAction -Execute 'powershell' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Alfred\scripts\morning-report.ps1'
# $trg = New-ScheduledTaskTrigger -Daily -At 7:00AM
# Register-ScheduledTask -TaskName 'Alfred-MorningReport' -Action $act -Trigger $trg -Description 'Alfred morning briefing'
