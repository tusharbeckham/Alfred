<#
.SYNOPSIS
  Alfred's voice - speak text aloud (fully offline) via the built-in Windows speech engine, or save to WAV.
.DESCRIPTION
  Uses System.Speech (Windows SAPI) - offline, no install, no API keys. Pipe Alfred's replies in to
  hear them. For a richer neural voice, see docs/local-coder or ask Alfred to set up Piper TTS.
.PARAMETER Text    What to say (positional, or pipe it in).
.PARAMETER Voice   Preferred installed voice name (partial match ok).
.PARAMETER Rate    Speaking rate -10..10 (default 1).
.PARAMETER Volume  Volume 0..100 (default 90).
.PARAMETER ToFile  Save to this .wav instead of playing aloud (great for testing quietly).
.PARAMETER List    List installed voices and exit.
.EXAMPLE  powershell -File scripts\alfred-say.ps1 "Good evening, sir."
.EXAMPLE  powershell -File scripts\alfred-say.ps1 -List
.EXAMPLE  powershell -File scripts\alfred-say.ps1 -ToFile test.wav "testing one two three"
#>
[CmdletBinding()]
param(
  [Parameter(Position=0, ValueFromPipeline=$true, ValueFromRemainingArguments=$true)][string[]]$Text,
  [string]$Voice,
  [int]$Rate = 1,
  [int]$Volume = 90,
  [string]$ToFile,
  [switch]$List
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer

try {
  if ($List) {
    $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } |
      Select-Object Name, Culture, Gender, Age | Format-Table -AutoSize | Out-String | Write-Output
    return
  }

  $msg = (@($Text) -join ' ').Trim()
  if (-not $msg) { Write-Error "Nothing to say. Try: alfred-say 'good evening, sir'."; exit 2 }

  # Voice: explicit partial match if given, else the engine default.
  if ($Voice) {
    $match = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name } |
             Where-Object { $_ -like "*$Voice*" } | Select-Object -First 1
    if ($match) { $synth.SelectVoice($match) } else { Write-Warning "Voice '$Voice' not found; using default." }
  }
  $synth.Rate   = [Math]::Max(-10, [Math]::Min(10, $Rate))
  $synth.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))

  if ($ToFile) {
    $synth.SetOutputToWaveFile($ToFile)
    $synth.Speak($msg)
    $synth.SetOutputToDefaultAudioDevice()
    Write-Output "Wrote: $ToFile"
  } else {
    $synth.Speak($msg)
  }
}
finally { $synth.Dispose() }
