<#
.SYNOPSIS
  Build a fine-tuning dataset (JSONL of chat pairs) for local-coder.

.DESCRIPTION
  Sources, in priority order:
    1. Curated examples in data/finetune/examples.md  (### PROMPT / ### OUTPUT / ### END)
       -- the high-quality primary source; grow this over time.
    2. (optional, -IncludeMemoryMining) weak pairs mined from memory/*.md: each fenced
       code block paired with the nearest preceding heading. Off by default (noisy).

  Output: data/finetune/train.jsonl and val.jsonl in chat format:
    {"messages":[{"role":"system","content":...},{"role":"user",...},{"role":"assistant",...}]}
  This format matches qwen2.5-coder's chat template and is what the Colab/Unsloth
  notebook expects. Writes ONLY inside C:\Alfred. Installs nothing.

.PARAMETER SourceFile   Curated examples file. Default data/finetune/examples.md
.PARAMETER OutDir       Output directory. Default data/finetune
.PARAMETER SystemPrompt System message baked into each training row.
.PARAMETER ValFraction Fraction held out for validation. Default 0.15
.PARAMETER SampleCount  How many rows to print as a preview. Default 2
.PARAMETER IncludeMemoryMining  Also mine memory/*.md (weak pairs). Default off.
.PARAMETER Seed         RNG seed for the shuffle/split. Default 42.

.EXAMPLE
  powershell -File scripts\build-finetune-jsonl.ps1
.EXAMPLE
  powershell -File scripts\build-finetune-jsonl.ps1 -IncludeMemoryMining -SampleCount 3
#>
[CmdletBinding()]
param(
    [string]$SourceFile   = 'data/finetune/examples.md',
    [string]$OutDir       = 'data/finetune',
    [string]$SystemPrompt = 'You are local-coder, a precise coding assistant. Return correct, minimal, working code in the requested language, matching a Windows/PowerShell-first, concise style.',
    [double]$ValFraction  = 0.15,
    [int]$SampleCount     = 2,
    [switch]$IncludeMemoryMining,
    [int]$Seed            = 42
)

$ErrorActionPreference = 'Stop'
Set-Location 'C:\Alfred'

function New-Row {
    param([string]$UserContent, [string]$AssistantContent)
    [ordered]@{
        messages = @(
            [ordered]@{ role = 'system';    content = $SystemPrompt },
            [ordered]@{ role = 'user';      content = $UserContent.Trim() },
            [ordered]@{ role = 'assistant'; content = $AssistantContent.Trim() }
        )
    }
}

$rows = New-Object System.Collections.Generic.List[object]

# ---- Source 1: curated examples ----------------------------------------------------
if (Test-Path -LiteralPath $SourceFile) {
    $raw = Get-Content -LiteralPath $SourceFile -Raw
    # Split into blocks on '### PROMPT' and parse each up to '### END'.
    $matches = [regex]::Matches(
        $raw,
        '(?ms)^\#\#\#\s*PROMPT\s*\r?\n(.*?)^\#\#\#\s*OUTPUT\s*\r?\n(.*?)^\#\#\#\s*END\s*$'
    )
    foreach ($m in $matches) {
        $p = $m.Groups[1].Value
        $o = $m.Groups[2].Value
        if ($p.Trim() -and $o.Trim()) { $rows.Add((New-Row -UserContent $p -AssistantContent $o)) }
    }
    Write-Host ("[build] curated examples parsed: {0}" -f $matches.Count) -ForegroundColor Cyan
}
else {
    Write-Host "[build] no curated file at $SourceFile (skipping)" -ForegroundColor Yellow
}

# ---- Source 2 (optional): weak pairs mined from memory/*.md ------------------------
if ($IncludeMemoryMining) {
    $mined = 0
    Get-ChildItem -Path 'memory' -Filter '*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
        $text = Get-Content -LiteralPath $_.FullName -Raw
        foreach ($cb in [regex]::Matches($text, '(?ms)```[a-zA-Z0-9]*\r?\n(.*?)```')) {
            $code = $cb.Groups[1].Value
            $before = $text.Substring(0, $cb.Index)
            $heading = ([regex]::Matches($before, '(?m)^\#{1,6}\s*(.+)$') | Select-Object -Last 1)
            $title = if ($heading) { $heading.Groups[1].Value.Trim() } else { $null }
            if ($title -and $code.Trim().Length -ge 20) {
                $rows.Add((New-Row -UserContent "Provide the code for: $title" -AssistantContent $code))
                $mined++
            }
        }
    }
    Write-Host ("[build] memory-mined weak pairs: {0}" -f $mined) -ForegroundColor Cyan
}

if ($rows.Count -eq 0) {
    Write-Host "[build] No examples found. Add pairs to $SourceFile and re-run." -ForegroundColor Red
    exit 1
}

# ---- Dedup (by user+assistant hash) ------------------------------------------------
$seen = @{}
$unique = New-Object System.Collections.Generic.List[object]
foreach ($r in $rows) {
    $key = ($r.messages[1].content + '||' + $r.messages[2].content)
    $sha = [System.BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash(
            [System.Text.Encoding]::UTF8.GetBytes($key)))
    if (-not $seen.ContainsKey($sha)) { $seen[$sha] = $true; $unique.Add($r) }
}

# ---- Shuffle + split ---------------------------------------------------------------
$rng = [System.Random]::new($Seed)
$shuffled = $unique | Sort-Object { $rng.Next() }
$valN = [int][math]::Floor($shuffled.Count * $ValFraction)
$val = @($shuffled | Select-Object -First $valN)
$train = @($shuffled | Select-Object -Skip $valN)

# ---- Write JSONL (one compact JSON object per line) --------------------------------
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$trainPath = Join-Path $OutDir 'train.jsonl'
$valPath   = Join-Path $OutDir 'val.jsonl'

function Write-Jsonl {
    param($Items, [string]$Path)
    $sw = [System.IO.StreamWriter]::new($Path, $false, [System.Text.UTF8Encoding]::new($false))
    try {
        foreach ($it in $Items) { $sw.WriteLine(($it | ConvertTo-Json -Depth 8 -Compress)) }
    } finally { $sw.Dispose() }
}

Write-Jsonl -Items $train -Path $trainPath
Write-Jsonl -Items $val   -Path $valPath

Write-Host ""
Write-Host ("[build] total unique rows : {0}" -f $unique.Count) -ForegroundColor Green
Write-Host ("[build] train             : {0}  -> {1}" -f $train.Count, $trainPath) -ForegroundColor Green
Write-Host ("[build] val               : {0}  -> {1}" -f $val.Count, $valPath) -ForegroundColor Green

# ---- Preview -----------------------------------------------------------------------
Write-Host ""
Write-Host "===== SAMPLE (first $SampleCount train rows, pretty-printed) =====" -ForegroundColor Magenta
$train | Select-Object -First $SampleCount | ForEach-Object {
    ($_ | ConvertTo-Json -Depth 8)
    Write-Host "-----"
}
Write-Host "===== SAMPLE (raw JSONL, first line as written) =====" -ForegroundColor Magenta
Get-Content -LiteralPath $trainPath -TotalCount 1
