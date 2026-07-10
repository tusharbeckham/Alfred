<#
.SYNOPSIS
  Alfred session capture - save several memories (decisions, learnings, traits) from one conversation
  in a single call. Each item is stored in memory/memory.jsonl (with a local embedding) AND the SQLite
  megamind (memory/megamind.db) via alfred-remember.ps1.
.DESCRIPTION
  The practical "capture the conversation" step: at the end of a session, record the key points so they
  persist and are instantly recallable. This is a DELIBERATE one-command capture, not a silent
  background hook - honest by design.
.PARAMETER Items
  One or more "type|topic|text|tags" strings. type in {decision,learning,fact,preference,outcome,note}.
.EXAMPLE
  powershell -NoProfile -File scripts/alfred-capture.ps1 `
    "decision|solar tests|Modernized the solar-forecast suite to 20 tests|solar,tests" `
    "preference|tone|Owner values honest verification over hype|owner,style"
#>
[CmdletBinding()]
param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Items)
$ErrorActionPreference = 'Continue'
$remember = Join-Path $PSScriptRoot 'alfred-remember.ps1'
$ok = 0; $fail = 0
foreach ($it in $Items) {
  $parts = $it -split '\|', 4
  if ($parts.Count -lt 3) { Write-Warning "Skipping malformed item (need 'type|topic|text[|tags]'): $it"; $fail++; continue }
  $type  = $parts[0].Trim()
  $topic = $parts[1].Trim()
  $text  = $parts[2].Trim()
  $tags  = if ($parts.Count -ge 4 -and $parts[3].Trim()) { $parts[3].Trim() -split '\s*,\s*' } else { @() }
  try { & $remember -Type $type -Topic $topic -Text $text -Tags $tags | Out-Null; $ok++ }
  catch { Write-Warning "capture failed for '$topic': $($_.Exception.Message)"; $fail++ }
}
Write-Output ("captured $ok memories ($fail failed) -> memory.jsonl + megamind.db")
