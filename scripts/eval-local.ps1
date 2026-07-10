<#
.SYNOPSIS
  Alfred — run an eval suite through the LOCAL model (Qwen via LM Studio) and write a review report.
.DESCRIPTION
  For each case in a suite JSON (e.g. evals/persona-evals.json, evals/coding-evals.json), sends the
  input to scripts/local-coder.ps1 and records the model's response beside the expected_criteria.
  Use it before and after a fine-tune to measure the local model. Grading is by review.
.PARAMETER Suite     Suite JSON path. Default evals/persona-evals.json
.PARAMETER Max       Limit number of cases (0 = all).
.PARAMETER MaxTokens Max tokens per response. Default 220.
.PARAMETER Recall    Inject Alfred memory into each prompt (local-coder -Recall).
.PARAMETER OutFile   Report path. Default evals/results/local-eval-<suite>-<timestamp>.md
.EXAMPLE
  powershell -NoProfile -File scripts/eval-local.ps1 -Suite evals/persona-evals.json
#>
[CmdletBinding()]
param(
  [string]$Suite = 'evals/persona-evals.json',
  [int]$Max = 0,
  [int]$MaxTokens = 220,
  [switch]$Recall,
  [string]$OutFile
)
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Alfred'
if (-not (Test-Path $Suite)) { Write-Error "Suite not found: $Suite"; exit 1 }

$data  = Get-Content $Suite -Raw | ConvertFrom-Json
$cases = @($data.cases)
if ($Max -gt 0) { $cases = @($cases | Select-Object -First $Max) }

if (-not $OutFile) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $name  = [IO.Path]::GetFileNameWithoutExtension($Suite)
  $OutFile = "evals/results/local-eval-$name-$stamp.md"
}
$dir = Split-Path $OutFile
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

$lc = Join-Path $PSScriptRoot 'local-coder.ps1'
$lines = @("# Local-model eval - suite: $($data.suite)", "", "Cases: $($cases.Count)  |  generated: $(Get-Date -Format o)", "")
$responses = [ordered]@{}
$i = 0
foreach ($c in $cases) {
  $i++
  Write-Host ("[eval-local] {0}/{1} {2}" -f $i, $cases.Count, $c.id)
  try {
    if ($Recall) { $resp = (& $lc -Recall $c.input -MaxTokens $MaxTokens 2>&1 | Out-String) }
    else         { $resp = (& $lc $c.input -MaxTokens $MaxTokens 2>&1 | Out-String) }
  } catch { $resp = "ERROR: $($_.Exception.Message)" }
  $responses[$c.id] = $resp.Trim()
  $lines += "## $($c.id) - $($c.category) (weight $($c.weight))"
  $lines += ""
  $lines += "**Input:** $($c.input)"
  $lines += ""
  $lines += "**Expected:** " + (@($c.expected_criteria) -join '; ')
  $lines += ""
  $lines += "**Model response:**"
  $lines += '```'
  $lines += $resp.Trim()
  $lines += '```'
  $lines += ""
}
$lines -join "`n" | Set-Content -LiteralPath $OutFile -Encoding UTF8
$respFile = [IO.Path]::ChangeExtension($OutFile, '.responses.json')
$responses | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $respFile -Encoding UTF8
Write-Host ("[eval-local] wrote {0} + {1} ({2} cases)" -f $OutFile, (Split-Path $respFile -Leaf), $cases.Count) -ForegroundColor Green
Write-Host ("[eval-local] score it:  python scripts/eval-score.py score --suite {0} --checks evals/<suite>-checks.json --responses {1}" -f $Suite, $respFile) -ForegroundColor DarkGray
