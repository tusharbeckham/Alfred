<#
.SYNOPSIS
  Alfred - engage online, SAFELY. Monitors current signal on a topic (keyless web + Reddit) and
  DRAFTS posts for the Owner to review and post himself. It NEVER posts, DMs, or logs into any
  account - no credentials, no network writes, no impersonation.
.DESCRIPTION
  Read + draft only. Alfred does not post as the Owner (inauthentic, and it gets accounts banned).
  Output is a local review doc: the sources it read + draft candidates + an APPROVAL-REQUIRED banner.
  The Owner reviews, edits, and posts himself if he approves.
.PARAMETER Topic     What to monitor / post about (required).
.PARAMETER Platform  generic | x | linkedin | reddit - tunes tone + length. Default generic.
.PARAMETER Count     Number of draft candidates. Default 2.
.PARAMETER OutDir    Where to save the review doc. Default memory/engage-drafts.
.PARAMETER NoModel   Skip local-model drafting (just gather signal + a template to fill in).
.EXAMPLE  powershell -File scripts\alfred-engage.ps1 -Topic "local LLM fine-tuning" -Platform x
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Topic,
  [ValidateSet('generic','x','linkedin','reddit')][string]$Platform = 'generic',
  [int]$Count = 2,
  [string]$OutDir = 'memory/engage-drafts',
  [switch]$NoModel
)
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$web    = Join-Path $PSScriptRoot 'alfred-web.ps1'
$reddit = Join-Path $PSScriptRoot 'alfred-reddit.ps1'
$lc     = Join-Path $PSScriptRoot 'local-coder.ps1'

Write-Host "[engage] gathering signal on: $Topic" -ForegroundColor DarkGray
$webResults    = try { & $web -Search $Topic -Max 5 2>&1 | Out-String } catch { "(web search unavailable)" }
$redditResults = try { & $reddit $Topic 2>&1 | Out-String }            catch { "(reddit signal unavailable)" }

$guide = switch ($Platform) {
  'x'        { 'a punchy post under 280 characters' }
  'linkedin' { 'a professional LinkedIn post, 3-5 short paragraphs' }
  'reddit'   { 'a helpful, non-marketing Reddit comment' }
  default    { 'a short, clear social post' }
}

$drafts = ''
if (-not $NoModel) {
  $prompt = "You are drafting social content for the Owner to REVIEW (never to auto-post). Topic: $Topic. " +
            "Platform: $Platform - write $guide. Draft $Count distinct options, numbered. Authentic and " +
            "value-adding; no hashtag spam, no fabricated stats or fake engagement. Base them on this signal:`n" +
            $webResults + "`n" + $redditResults
  Write-Host "[engage] drafting with the local model..." -ForegroundColor DarkGray
  $drafts = try { & powershell -NoProfile -ExecutionPolicy Bypass -File $lc -MaxTokens 320 $prompt 2>&1 | Out-String } catch { '' }
}
if (-not $drafts.Trim()) {
  $drafts = "1. [DRAFT - write here, grounded in the signal below]`n`n2. [DRAFT - a second angle]"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp     = Get-Date -Format 'yyyyMMdd-HHmmss'
$safeTopic = ($Topic -replace '[^\w\-]+','-').Trim('-'); if ($safeTopic.Length -gt 40) { $safeTopic = $safeTopic.Substring(0,40) }
$out       = Join-Path $OutDir "$stamp-$safeTopic.md"

$doc = @"
# Engage draft - $Topic

> **STATUS: DRAFT for the Owner's review. NOT posted.** Alfred does not post, DM, or log into any
> account, and never posts as you. Review, edit, and post it yourself if you approve.

- Platform: $Platform   |   Generated: $(Get-Date -Format o)

## Draft candidates (review before using)
$drafts

## Signal read - web
$webResults

## Signal read - Reddit
$redditResults
"@
$doc | Set-Content -LiteralPath $out -Encoding UTF8
Write-Host "[engage] wrote review doc: $out" -ForegroundColor Green
Write-Host "[engage] Review + post it yourself if approved. Alfred will not post as you." -ForegroundColor Cyan
