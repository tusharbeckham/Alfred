<#
.SYNOPSIS
  Alfred megamind — recall the most relevant memories for a query (offline semantic search).
.DESCRIPTION
  Embeds the query with the local nomic model and ranks memory/memory.jsonl by cosine similarity.
  Works fully offline (no internet, no Kiro). Falls back to keyword match if embeddings are unavailable.
  Used by scripts/local-coder.ps1 -Recall to give the local model long-term memory.
.EXAMPLE
  powershell -File scripts/alfred-recall.ps1 -Query "what does the Owner want to be called?" -TopK 3
.EXAMPLE
  powershell -File scripts/alfred-recall.ps1 -Query "which local model do we use" -Raw
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Query,
  [int]$TopK = 4,
  [double]$MinScore = 0.30,
  [switch]$Raw,
  [string]$Store = "$PSScriptRoot\..\memory\memory.jsonl",
  [string]$BaseUrl = 'http://localhost:1234/v1',
  [string]$EmbedModel = 'text-embedding-nomic-embed-text-v1.5'
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Store)) { Write-Output "(no memories yet)"; return }
$entries = @(Get-Content -LiteralPath $Store | Where-Object { $_.Trim() } | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} })
if (-not $entries -or $entries.Count -eq 0) { Write-Output "(no memories yet)"; return }

function Get-Cosine($a, $b) {
  if (-not $a -or -not $b -or $a.Count -ne $b.Count) { return -1 }
  $dot = 0.0; $na = 0.0; $nb = 0.0
  for ($i = 0; $i -lt $a.Count; $i++) { $dot += $a[$i]*$b[$i]; $na += $a[$i]*$a[$i]; $nb += $b[$i]*$b[$i] }
  if ($na -eq 0 -or $nb -eq 0) { return -1 }
  return $dot / ([math]::Sqrt($na) * [math]::Sqrt($nb))
}

$q = $null
try {
  $body = @{ model = $EmbedModel; input = $Query } | ConvertTo-Json
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
  $r = Invoke-RestMethod "$BaseUrl/embeddings" -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 30
  $q = @($r.data[0].embedding)
} catch { }

if ($q) {
  $scored = foreach ($e in $entries) {
    $sc = if ($e.embedding -and @($e.embedding).Count -gt 0) { Get-Cosine $q @($e.embedding) } else { -1 }
    [pscustomobject]@{ score = $sc; type = $e.type; topic = $e.topic; text = $e.text }
  }
  $thr = $MinScore
} else {
  $terms = @($Query -split '\W+' | Where-Object { $_.Length -gt 2 })
  $scored = foreach ($e in $entries) {
    $hay = "$($e.topic) $($e.text)"
    $hits = @($terms | Where-Object { $hay -match [regex]::Escape($_) }).Count
    [pscustomobject]@{ score = $hits; type = $e.type; topic = $e.topic; text = $e.text }
  }
  $thr = 1
}

$top = @($scored | Sort-Object score -Descending | Select-Object -First $TopK | Where-Object { $_.score -ge $thr })
if (-not $top -or $top.Count -eq 0) { Write-Output "(no relevant memories)"; return }

if ($Raw) { $top | ForEach-Object { $_.text }; return }
$top | ForEach-Object {
  $s = if ($_.score -ge 0 -and $q) { " (~" + [math]::Round($_.score,2) + ")" } else { "" }
  Write-Output ("- [{0}] {1}{2}: {3}" -f $_.type, $_.topic, $s, $_.text)
}
