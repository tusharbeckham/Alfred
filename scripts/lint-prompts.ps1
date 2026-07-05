<#
.SYNOPSIS
  Guardrail lint — flag Alfred prompt/skill/steering files that exceed a line budget.

.DESCRIPTION
  The eval-driven self-improvement loop APPENDS text to prompts on each accepted cycle
  (e.g. the researcher + coder cycles on 2026-07-05). Left unchecked this bloats prompts,
  which hurts the rubric's conciseness score and raises token cost every run. This lint
  reports line counts and flags anything over -MaxLines so bloat is caught early.

  Read-only. Exit 0 = all within budget; exit 1 = at least one file over budget (so it can
  gate a future hook if desired). Installs nothing; touches nothing.

.PARAMETER MaxLines  Per-file budget (default 120, per the RSI design's Phase-2 note).
.EXAMPLE
  powershell -File scripts\lint-prompts.ps1
  powershell -File scripts\lint-prompts.ps1 -MaxLines 80
#>
[CmdletBinding()]
param([int]$MaxLines = 120)

$ErrorActionPreference = 'Stop'
Set-Location 'C:\Alfred'

$targets = @()
$targets += Get-ChildItem '.kiro/brains'   -Recurse -Filter 'identity.txt' -File -ErrorAction SilentlyContinue
$targets += Get-ChildItem '.kiro/steering' -Filter '*.md'   -File -ErrorAction SilentlyContinue
$targets += Get-ChildItem '.kiro/skills'   -Recurse -Filter 'SKILL.md'     -File -ErrorAction SilentlyContinue
$targets += Get-ChildItem 'prompts'        -Recurse -Filter '*.txt'        -File -ErrorAction SilentlyContinue

$root = (Get-Location).Path + '\'
$rows = foreach ($f in $targets) {
    $n = (Get-Content -LiteralPath $f.FullName | Measure-Object -Line).Lines
    [pscustomobject]@{
        Lines  = $n
        Status = if ($n -gt $MaxLines) { 'OVER' } else { 'ok' }
        File   = $f.FullName.Replace($root, '')
    }
}

$rows = $rows | Sort-Object Lines -Descending
$rows | Format-Table -AutoSize | Out-String | Write-Output

$over = @($rows | Where-Object { $_.Status -eq 'OVER' })
Write-Output ("[lint] scanned {0} files, budget {1} lines, {2} over budget." -f $rows.Count, $MaxLines, $over.Count)
if ($over.Count -gt 0) {
    Write-Output ("[lint] OVER: " + (($over | ForEach-Object { $_.File }) -join ', '))
    exit 1
}
exit 0
