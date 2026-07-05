<#
.SYNOPSIS
  Alfred Mail — read / compose / send for the Owner's Gmail. SENDING is gated.

.DESCRIPTION
  Credentials load from secrets/mail.json (git-ignored, never printed). By default Alfred
  READS your unread inbox and shows a composed DRAFT for review; it SENDS only with
  -Send AND -Confirm. Zero external dependencies (uses .NET SMTP + Gmail's authenticated
  Atom feed for unread mail).

.EXAMPLE  powershell -File scripts\alfred-mail.ps1 -Read
.EXAMPLE  powershell -File scripts\alfred-mail.ps1 -To a@b.com -Subject "Hi" -Body "Hello"          # draft preview
.EXAMPLE  powershell -File scripts\alfred-mail.ps1 -To a@b.com -Subject "Hi" -Body "Hello" -Send -Confirm
#>
[CmdletBinding()]
param(
    [switch]$Read,
    [int]$Count = 10,
    [string]$To,
    [string]$Subject,
    [string]$Body = '',
    [switch]$Send,
    [switch]$Confirm,
    [string]$CredPath = (Join-Path $PSScriptRoot '..\secrets\mail.json')
)
$ErrorActionPreference = 'Stop'
function Die($m, $code) { Write-Host $m -ForegroundColor Red; exit $code }

if (-not (Test-Path $CredPath)) { Die "No credentials at $CredPath. See docs\email-setup.md (create an App Password + this file)." 3 }
try { $c = Get-Content $CredPath -Raw | ConvertFrom-Json } catch { Die "secrets\mail.json is not valid JSON." 3 }
foreach ($f in 'email', 'app_password') { if (-not $c.$f) { Die "secrets\mail.json is missing '$f'." 3 } }
$smtpHost = if ($c.smtp_host) { $c.smtp_host } else { 'smtp.gmail.com' }
$smtpPort = if ($c.smtp_port) { [int]$c.smtp_port } else { 587 }

# ---- READ: unread inbox via Gmail's authenticated Atom feed (no libraries) ----
if ($Read) {
    $sec  = ConvertTo-SecureString $c.app_password -AsPlainText -Force
    $cred = New-Object System.Management.Automation.PSCredential($c.email, $sec)
    try { $feed = Invoke-RestMethod 'https://mail.google.com/mail/feed/atom' -Credential $cred -TimeoutSec 25 }
    catch { Die ("Could not read mail: " + $_.Exception.Message + "  (Verify the App Password and that access is allowed.)") 1 }
    Write-Host ("Unread in inbox: " + $feed.feed.fullcount) -ForegroundColor Cyan
    $entries = @($feed.feed.entry)
    if ($entries.Count -eq 0) { Write-Host "No unread messages."; exit 0 }
    $i = 0
    foreach ($e in ($entries | Select-Object -First $Count)) {
        $i++
        $from = if ($e.author) { "$($e.author.name) <$($e.author.email)>" } else { '' }
        $sum  = if ($e.summary) { ($e.summary -replace '\s+', ' ').Trim() } else { '' }
        if ($sum.Length -gt 120) { $sum = $sum.Substring(0, 120) + '...' }
        Write-Host ("[{0}] {1}" -f $i, $e.title) -ForegroundColor White
        Write-Host ("     from: {0}" -f $from) -ForegroundColor DarkGray
        if ($sum) { Write-Host ("     {0}" -f $sum) -ForegroundColor DarkGray }
    }
    exit 0
}

# ---- COMPOSE: draft preview by default; send only with -Send -Confirm ----
if (-not $To -or -not $Subject) { Die 'Usage:  -Read  |  -To <addr> -Subject <s> -Body <b> [-Send -Confirm]' 2 }

if (-not $Send) {
    Write-Host "----- DRAFT (not sent) -----" -ForegroundColor Yellow
    Write-Host ("From:    " + $c.email)
    Write-Host ("To:      " + $To)
    Write-Host ("Subject: " + $Subject)
    Write-Host "---"
    Write-Host $Body
    Write-Host "----- To actually send, re-run with:  -Send -Confirm -----" -ForegroundColor Yellow
    exit 0
}
if ($Send -and -not $Confirm) { Die "Refusing to send without -Confirm. Re-run with -Send -Confirm to actually send." 2 }

try {
    $smtp = New-Object System.Net.Mail.SmtpClient($smtpHost, $smtpPort)
    $smtp.EnableSsl = $true
    $smtp.Credentials = New-Object System.Net.NetworkCredential($c.email, $c.app_password)
    $mail = New-Object System.Net.Mail.MailMessage($c.email, $To, $Subject, $Body)
    $smtp.Send($mail)
    $mail.Dispose(); $smtp.Dispose()
    Write-Host ("SENT to {0}: {1}" -f $To, $Subject) -ForegroundColor Green
}
catch { Die ("Send failed: " + $_.Exception.Message) 1 }
