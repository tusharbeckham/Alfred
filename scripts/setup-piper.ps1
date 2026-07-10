<#
.SYNOPSIS
  Set up Piper neural TTS locally (the offline voice used by alfred-say). Downloads the Piper Windows
  binary and a voice model into tools/piper. Re-runnable; skips anything already present.
.PARAMETER Voice  Piper voice id (default en_GB-alan-medium = deep British male). Other good males:
  en_US-ryan-high, en_GB-northern_english_male-medium. Browse https://huggingface.co/rhasspy/piper-voices
.EXAMPLE  powershell -File scripts\setup-piper.ps1
.EXAMPLE  powershell -File scripts\setup-piper.ps1 -Voice en_US-ryan-high
#>
[CmdletBinding()]
param(
  [string]$Voice = 'en_GB-alan-medium',
  [string]$PiperRelease = '2023.11.14-2'
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..\tools\piper'
$vdir = Join-Path $root 'voices'
New-Item -ItemType Directory -Force -Path $vdir | Out-Null

# 1) Piper binary
$exe = Join-Path $root 'piper\piper.exe'
if (-not (Test-Path $exe)) {
  $zip = Join-Path $env:TEMP 'piper_win.zip'
  $url = "https://github.com/rhasspy/piper/releases/download/$PiperRelease/piper_windows_amd64.zip"
  Write-Host "Downloading Piper binary ($PiperRelease)..." -ForegroundColor DarkGray
  Invoke-WebRequest -Uri $url -OutFile $zip -TimeoutSec 300 -UseBasicParsing
  Expand-Archive -Path $zip -DestinationPath $root -Force
  Remove-Item $zip -ErrorAction SilentlyContinue
}
if (Test-Path $exe) { Write-Host "  piper.exe ready." -ForegroundColor Green } else { throw "piper.exe missing after download." }

# 2) Voice model - id format: <lang>_<REGION>-<name>-<quality>, e.g. en_GB-alan-medium
$onnx = Join-Path $vdir "$Voice.onnx"
if (-not (Test-Path $onnx)) {
  if ($Voice -match '^([a-z]{2})_([A-Z]{2})-(.+)-([a-z]+)$') {
    $lang = $Matches[1]; $region = $Matches[2]; $name = $Matches[3]; $qual = $Matches[4]
    $vp   = "$lang/${lang}_$region/$name/$qual/$Voice"
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/$vp"
    Write-Host "Downloading voice '$Voice'..." -ForegroundColor DarkGray
    Invoke-WebRequest -Uri "$base.onnx"      -OutFile $onnx        -TimeoutSec 600 -UseBasicParsing
    Invoke-WebRequest -Uri "$base.onnx.json" -OutFile "$onnx.json" -TimeoutSec 120 -UseBasicParsing
  } else { throw "Voice id '$Voice' is not in <lang>_<REGION>-<name>-<quality> form." }
}
if (Test-Path $onnx) { Write-Host ("  voice ready: {0} ({1} MB)" -f $Voice, [math]::Round((Get-Item $onnx).Length/1MB,1)) -ForegroundColor Green } else { throw "voice model missing." }

Write-Host 'Piper ready. Test:  powershell -File scripts\alfred-say.ps1 "Good evening, sir."' -ForegroundColor Cyan
