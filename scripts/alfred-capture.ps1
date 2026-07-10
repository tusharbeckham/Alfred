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
  powershell -NoProfile -File scripts/alfred-capture.ps1 -Items @(
    "decision|solar tests|Modernized the solar-forecast suite to 20 tests covering POA + forecast API|solar,tests",
    "preference|tone|Owner values honest verification over hype|owner,style"
  )
#>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string[]]$Items)
$ErrorActionPreference = 'Stop'
$remember = Join-Path $PSScriptRoot 'alfred-remember.ps1'
$n = 0
foreach ($it in $Items) {
  $parts = $it -split '\|', 4
  if ($parts.Count -lt 3) { Write-Warning "Skipping malformed item (need 'type|topic|text[|tags]'): $it"; continue }
  $type  = $parts[0].Trim()
  $topic = $parts[1].Trim()
  $text  = $parts[2].Trim()
  $tags  = if ($parts.Count -ge 4 -and $parts[3].Trim()) { $parts[3].Trim() -split '\s*,\s*' } else { @() }
  & $remember -Type $type -Topic $topic -Text $text -Tags $tags | Out-Null
  $n++
}
Write-Output ("captured $n memories (memory.jsonl + megamind.db)")
