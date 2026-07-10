<#
.SYNOPSIS
  Alfred's voice - speak text aloud (offline). Prefers Piper neural TTS (deep British male voice),
  falls back to the built-in Windows engine (SAPI). Can also save to a WAV.
.DESCRIPTION
  Piper = free, local, neural voice under tools/piper (no keys, offline). SAPI = built-in Windows
  voices (always available). Pipe Alfred's replies in to hear them.
.PARAMETER Text    What to say (positional, or pipe it in).
.PARAMETER Engine  auto (default) | piper | sapi.  auto = Piper if installed, else SAPI.
.PARAMETER Voice   Piper: .onnx model path or filename in the voices dir. SAPI: voice name (partial ok).
.PARAMETER Rate    SAPI speaking rate -10..10 (default 1). Ignored by Piper.
.PARAMETER Volume  SAPI volume 0..100 (default 90). Ignored by Piper.
.PARAMETER ToFile  Save to this .wav instead of playing aloud.
.PARAMETER List    List available voices (Piper models + SAPI voices) and exit.
.EXAMPLE  powershell -File scripts\alfred-say.ps1 "Good evening, sir."
.EXAMPLE  powershell -File scripts\alfred-say.ps1 -List
.EXAMPLE  powershell -File scripts\alfred-say.ps1 -Engine sapi -Voice David "hello"
#>
[CmdletBinding()]
param(
  [Parameter(Position=0, ValueFromPipeline=$true, ValueFromRemainingArguments=$true)][string[]]$Text,
  [ValidateSet('auto','piper','sapi')][string]$Engine = 'auto',
  [string]$Voice,
  [int]$Rate = 1,
  [int]$Volume = 90,
  [string]$ToFile,
  [switch]$List
)
$ErrorActionPreference = 'Continue'   # piper.exe logs info to stderr; do not treat that as fatal

$PiperExe      = Join-Path $PSScriptRoot '..\tools\piper\piper\piper.exe'
$PiperVoiceDir = Join-Path $PSScriptRoot '..\tools\piper\voices'
$PiperDefault  = Join-Path $PiperVoiceDir 'en_GB-alan-medium.onnx'
function Test-Piper { (Test-Path $PiperExe) -and (Test-Path $PiperDefault) }

if ($List) {
  Write-Output "Piper (neural) voices:"
  if (Test-Path $PiperVoiceDir) {
    $m = Get-ChildItem $PiperVoiceDir -Filter *.onnx -ErrorAction SilentlyContinue
    if ($m) { $m | ForEach-Object { Write-Output ("  - " + $_.BaseName) } } else { Write-Output "  (none installed)" }
  } else { Write-Output "  (none installed)" }
  Write-Output "SAPI (built-in) voices:"
  Add-Type -AssemblyName System.Speech
  (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() |
    ForEach-Object { Write-Output ("  - " + $_.VoiceInfo.Name + " (" + $_.VoiceInfo.Gender + ")") }
  return
}

$msg = (@($Text) -join ' ').Trim()
if (-not $msg) { Write-Error "Nothing to say. Try: alfred-say 'good evening, sir'."; exit 2 }

if ($Engine -eq 'auto') { $Engine = if (Test-Piper) { 'piper' } else { 'sapi' } }

if ($Engine -eq 'piper') {
  if (-not (Test-Piper)) { Write-Error "Piper not installed at $PiperExe. Use -Engine sapi."; exit 3 }
  $model = if ($Voice) { if (Test-Path $Voice) { $Voice } else { Join-Path $PiperVoiceDir $Voice } } else { $PiperDefault }
  if (-not (Test-Path $model)) { Write-Error "Piper voice model not found: $model"; exit 4 }
  $out = if ($ToFile) { $ToFile } else { Join-Path $env:TEMP ("alfred_say_" + [guid]::NewGuid().ToString('N') + ".wav") }
  $msg | & $PiperExe --model $model --output_file $out 2>&1 | Out-Null
  if (-not (Test-Path $out) -or (Get-Item $out).Length -eq 0) { Write-Error "Piper synthesis failed (empty output)."; exit 5 }
  if ($ToFile) { Write-Output "Wrote: $out" }
  else {
    (New-Object System.Media.SoundPlayer $out).PlaySync()
    Remove-Item $out -ErrorAction SilentlyContinue
  }
  return
}

# SAPI (built-in Windows engine)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  if ($Voice) {
    $match = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name } |
             Where-Object { $_ -like "*$Voice*" } | Select-Object -First 1
    if ($match) { $synth.SelectVoice($match) } else { Write-Warning "Voice '$Voice' not found; using default." }
  }
  $synth.Rate   = [Math]::Max(-10, [Math]::Min(10, $Rate))
  $synth.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))
  if ($ToFile) { $synth.SetOutputToWaveFile($ToFile); $synth.Speak($msg); $synth.SetOutputToDefaultAudioDevice(); Write-Output "Wrote: $ToFile" }
  else { $synth.Speak($msg) }
} finally { $synth.Dispose() }
