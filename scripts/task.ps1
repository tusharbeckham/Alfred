<#
.SYNOPSIS
  Alfred task - a tiny backlog manager for memory/todo.md (list / add / done). Offline.
.DESCRIPTION
  Reads and updates the project backlog in memory/todo.md without spawning an agent. Only
  project-scoped, non-destructive items belong there (see safety.md). Editing this file is the
  sanctioned way the overnight run picks up work.
    list          show pending [ ], in-progress [~], and needs-owner [!] items (default)
    add "<text>"  append a new pending item under ## Backlog
    done <n>      mark the n-th pending item complete [x]
.EXAMPLE  powershell -File scripts\task.ps1 list
.EXAMPLE  powershell -File scripts\task.ps1 add "Add a smoke test for alfred-web.ps1"
.EXAMPLE  powershell -File scripts\task.ps1 done 1
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][ValidateSet('list','add','done')][string]$Command = 'list',
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)
$ErrorActionPreference = 'Stop'
$todo = 'C:\Alfred\memory\todo.md'
if (-not (Test-Path $todo)) { Write-Error "todo.md not found at $todo"; exit 1 }

function Get-Pending($lines) {
  $out = @()
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*-\s*\[\s\]') { $out += $i }
  }
  return ,$out
}

$lines = Get-Content -LiteralPath $todo

switch ($Command) {
  'list' {
    Write-Host "-- Pending --" -ForegroundColor Cyan
    $p = Get-Pending $lines; $n = 1
    foreach ($i in $p) { Write-Host ("  {0}. {1}" -f $n, ($lines[$i] -replace '^\s*-\s*\[\s\]\s*','')); $n++ }
    if ($p.Count -eq 0) { Write-Host "  (none)" }
    Write-Host "-- In progress --" -ForegroundColor Cyan
    $lines | Where-Object { $_ -match '^\s*-\s*\[~\]' } | ForEach-Object { Write-Host ("  " + ($_ -replace '^\s*-\s*\[~\]\s*','')) }
    Write-Host "-- Needs Owner --" -ForegroundColor Yellow
    $lines | Where-Object { $_ -match '^\s*-\s*\[!\]' } | ForEach-Object { Write-Host ("  " + ($_ -replace '^\s*-\s*\[!\]\s*','')) -ForegroundColor Yellow }
  }
  'add' {
    $text = ($Rest -join ' ').Trim()
    if (-not $text) { Write-Error "Provide task text: task.ps1 add ""...""" ; exit 1 }
    $idx = ($lines | Select-String -SimpleMatch '## Backlog' | Select-Object -First 1).LineNumber
    if (-not $idx) { Write-Error "No '## Backlog' section in todo.md"; exit 1 }
    $new = @()
    $new += $lines[0..($idx-1)]
    $new += "- [ ] $text"
    if ($idx -lt $lines.Count) { $new += $lines[$idx..($lines.Count-1)] }
    Set-Content -LiteralPath $todo -Value $new -Encoding UTF8
    Write-Host "[task] added: $text" -ForegroundColor Green
  }
  'done' {
    $num = 0
    if (-not [int]::TryParse(($Rest | Select-Object -First 1), [ref]$num) -or $num -lt 1) {
      Write-Error "Provide the pending item number: task.ps1 done <n>"; exit 1
    }
    $p = Get-Pending $lines
    if ($num -gt $p.Count) { Write-Error "Only $($p.Count) pending item(s)."; exit 1 }
    $target = $p[$num-1]
    $lines[$target] = $lines[$target] -replace '\[\s\]', '[x]'
    Set-Content -LiteralPath $todo -Value $lines -Encoding UTF8
    Write-Host ("[task] done: " + ($lines[$target] -replace '^\s*-\s*\[x\]\s*','')) -ForegroundColor Green
  }
}
