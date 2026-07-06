<#
.SYNOPSIS
  Alfred web access — keyless web search (DuckDuckGo) + readable page fetch.
.DESCRIPTION
  Gives Alfred agents (and the local path when the PC is online) real web access with no API key.
  -Search returns top results (rank, title, url). -Fetch returns a URL's readable text (tags stripped).
  Any agent can call it via its shell tool:
      powershell -NoProfile -File scripts/alfred-web.ps1 -Search "<query>"
      powershell -NoProfile -File scripts/alfred-web.ps1 -Fetch  "<url>"
.PARAMETER Search    Query string for web search.
.PARAMETER Fetch     URL to fetch and convert to readable text.
.PARAMETER Max       Max search results (default 8).
.PARAMETER MaxChars  Max characters returned by -Fetch (default 4000).
#>
[CmdletBinding()]
param(
  [string]$Search,
  [string]$Fetch,
  [int]$Max = 8,
  [int]$MaxChars = 4000
)
$ErrorActionPreference = 'Stop'
$UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

function Convert-HtmlToText([string]$html) {
  if (-not $html) { return '' }
  $html = [regex]::Replace($html, '(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ')
  $html = [regex]::Replace($html, '(?is)<br\s*/?>', "`n")
  $html = [regex]::Replace($html, '(?is)</(p|div|li|h[1-6]|tr)>', "`n")
  $html = [regex]::Replace($html, '(?s)<[^>]+>', ' ')
  $html = [System.Net.WebUtility]::HtmlDecode($html)
  $html = [regex]::Replace($html, '[ \t]+', ' ')
  $html = [regex]::Replace($html, '(\r?\n\s*){2,}', "`n`n")
  return $html.Trim()
}

if ($Search) {
  $u = "https://lite.duckduckgo.com/lite/?q=" + [uri]::EscapeDataString($Search)
  $r = Invoke-WebRequest $u -UseBasicParsing -UserAgent $UA -TimeoutSec 20
  $rx = [regex]::Matches($r.Content, '(?is)<a[^>]*href="([^"]*uddg=[^"]*)"[^>]*>(.*?)</a>')
  $seen = @{}; $n = 0
  foreach ($m in $rx) {
    $mm = [regex]::Match($m.Groups[1].Value, 'uddg=([^&]+)')
    if (-not $mm.Success) { continue }
    $url = [uri]::UnescapeDataString($mm.Groups[1].Value)
    if ($seen.ContainsKey($url)) { continue }
    $seen[$url] = $true
    $title = (Convert-HtmlToText $m.Groups[2].Value).Trim()
    if (-not $title) { continue }
    $n++
    Write-Output ("{0}. {1}" -f $n, $title)
    Write-Output ("   {0}" -f $url)
    if ($n -ge $Max) { break }
  }
  if ($n -eq 0) { Write-Output "No results parsed (DuckDuckGo layout may have changed)." }
  return
}

if ($Fetch) {
  $r = Invoke-WebRequest $Fetch -UseBasicParsing -UserAgent $UA -TimeoutSec 25
  $text = Convert-HtmlToText $r.Content
  if ($text.Length -gt $MaxChars) { $text = $text.Substring(0, $MaxChars) + "`n...[truncated]" }
  Write-Output $text
  return
}

Write-Output "Usage: -Search '<query>' [-Max N]  |  -Fetch '<url>' [-MaxChars N]"
