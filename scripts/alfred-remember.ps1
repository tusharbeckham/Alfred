<#
.SYNOPSIS
  Alfred megamind — capture a memory (reason/decision/fact/preference/outcome) with a local embedding.
.DESCRIPTION
  Appends a structured entry to memory/memory.jsonl and computes its embedding via LM Studio's local
  nomic model, so it can be recalled semantically OFFLINE later (no cloud, no Kiro needed). If the
  embedding endpoint is down, the entry is still stored (recall falls back to keyword match).
.EXAMPLE
  powershell -File scripts/alfred-remember.ps1 -Type preference -Topic "form of address" -Text "The Owner is addressed as 'sir', never 'champ'." -Tags owner,style
.EXAMPLE
  powershell -File scripts/alfred-remember.ps1 -Type decision -Topic "local base model" -Text "Adopted Qwen2.5-Coder-7B because Granite won't fine-tune on a free Kaggle T4."
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Text,
  [string]$Type = 'note',
  [string]$Topic = '',
  [string[]]$Tags = @(),
  [string]$Store = "$PSScriptRoot\..\memory\memory.jsonl",
  [string]$BaseUrl = 'http://localhost:1234/v1',
  [string]$EmbedModel = 'text-embedding-nomic-embed-text-v1.5'
)
$ErrorActionPreference = 'Stop'

$emb = @()
try {
  $body = @{ model = $EmbedModel; input = $Text } | ConvertTo-Json
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
  $r = Invoke-RestMethod "$BaseUrl/embeddings" -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 30
  $emb = @($r.data[0].embedding)
} catch {
  Write-Warning "Embedding unavailable (LM Studio down?); storing entry without a vector."
}

$entry = [ordered]@{
  id        = [guid]::NewGuid().ToString('n').Substring(0,12)
  ts        = (Get-Date).ToString('o')
  type      = $Type
  topic     = $Topic
  text      = $Text
  tags      = @($Tags)
  embedding = $emb
}
$dir = Split-Path $Store
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
($entry | ConvertTo-Json -Depth 6 -Compress) | Add-Content -LiteralPath $Store -Encoding UTF8

# Dual-write to the fast local SQLite megamind (best-effort) so FTS recall stays in sync, offline.
# Build the argument list explicitly: an empty -g value gets dropped by PowerShell's native-command
# argument handling, which made argparse fail and silently skipped every sync.
$mmArgs = @("$PSScriptRoot\megamind.py", 'add', '-T', $Type, '-o', $Topic, '-x', $Text)
$tagList = (@($Tags) | Where-Object { $_ }) -join ','
if ($tagList) { $mmArgs += @('-g', $tagList) }

$mmSynced = $false
try {
  $mmOut = & python @mmArgs 2>&1
  if ($LASTEXITCODE -eq 0) {
    $mmSynced = $true
  } else {
    Write-Warning "megamind.db sync FAILED (exit $LASTEXITCODE): $($mmOut -join ' ')"
  }
} catch { Write-Warning "megamind.db sync FAILED ($($_.Exception.Message))." }

$syncNote = if ($mmSynced) { 'synced to megamind.db' } else { 'NOT synced to megamind.db' }
Write-Output ("remembered [{0}] {1}  (embedding dims={2}; {3})" -f $Type, $Topic, $emb.Count, $syncNote)
