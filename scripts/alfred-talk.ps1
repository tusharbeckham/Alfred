<#
.SYNOPSIS
  Alfred - voice conversation. Type a message; Alfred replies in text AND speaks it aloud (offline).
  Type 'exit', 'quit', 'bye', or just press Enter on a blank line to leave.
.DESCRIPTION
  A local, fully-offline back-and-forth: your input -> local model reply -> spoken in Alfred's voice.
  Uses scripts/local-coder.ps1 (model) + scripts/alfred-say.ps1 (Piper voice). No cloud, no keys.
  Replies are kept short since they're spoken. CPU inference means a few seconds per turn.
.PARAMETER MaxTokens  Max reply length. Default 160 (short, spoken).
.PARAMETER Voice      Piper voice name override (optional).
.EXAMPLE  talk
#>
[CmdletBinding()]
param(
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Question,
  [int]$MaxTokens = 160,
  [string]$Voice
)
$ErrorActionPreference = 'Continue'
$say = Join-Path $PSScriptRoot 'alfred-say.ps1'
$lc  = Join-Path $PSScriptRoot 'local-coder.ps1'

$sys = 'You are Alfred, the Owner''s warm, witty British butler AI. Always address him as "sir". Keep replies SHORT and conversational - 1 to 3 sentences - because they are spoken aloud. Be honest and helpful; a little dry humour is welcome, but never fake certainty.'

function Speak($text) {
  if (-not $text) { return }
  $a = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$say)
  if ($Voice) { $a += @('-Voice',$Voice) }
  $a += $text
  & powershell @a 2>$null
}

Write-Host "Alfred voice chat - type a message, or 'exit' to leave." -ForegroundColor Cyan

# One-shot mode: a question was passed (e.g. `ask "how do I list files?"`) - answer once + speak, then exit.
if ($Question -and ($Question -join '').Trim()) {
  $q = ($Question -join ' ').Trim()
  Write-Host "Alfred is thinking..." -ForegroundColor DarkGray
  $reply = (& powershell -NoProfile -ExecutionPolicy Bypass -File $lc -System $sys -MaxTokens $MaxTokens $q 2>&1 | Out-String).Trim()
  if ($reply) { Write-Host ("Alfred: " + $reply) -ForegroundColor Green; Speak $reply }
  else { Write-Host "Alfred: (no reply - is the local model up?)" -ForegroundColor Yellow }
  return
}

Speak "Good evening, sir. I'm listening."

while ($true) {
  Write-Host ""
  $inp = Read-Host "You"
  if (-not $inp -or $inp.Trim() -match '^(exit|quit|bye)$') {
    Speak "Very good, sir. Call me when you need me."
    break
  }
  Write-Host "Alfred is thinking..." -ForegroundColor DarkGray
  $reply = (& powershell -NoProfile -ExecutionPolicy Bypass -File $lc -System $sys -MaxTokens $MaxTokens $inp 2>&1 | Out-String).Trim()
  if ($reply) {
    Write-Host ("Alfred: " + $reply) -ForegroundColor Green
    Speak $reply
  } else {
    Write-Host "Alfred: (no reply - is the local model up?)" -ForegroundColor Yellow
  }
}
