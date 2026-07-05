<#
.SYNOPSIS
  Alfred Mail — read / compose / send for the Owner's Gmail. SENDING is gated.

.DESCRIPTION
  Credentials load from secrets/mail.json (git-ignored, never printed). READ uses raw IMAP
  over SSL (no external libraries). SEND uses .NET SMTP and only fires with -Send AND -Confirm.

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
    [string]$CredPath = 'C:\Alfred\secrets\mail.json'
)
$ErrorActionPreference = 'Stop'
function Die($m, $code) { Write-Host $m -ForegroundColor Red; exit $code }
function Read-ImapUntil($sr, $tag) {
    $sb = New-Object System.Text.StringBuilder
    while ($true) { $l = $sr.ReadLine(); if ($null -eq $l) { break }; [void]$sb.AppendLine($l); if ($l -match "^$tag (OK|NO|BAD)") { break } }
    $sb.ToString()
}

if (-not (Test-Path $CredPath)) { Die "No credentials at $CredPath. See docs\email-setup.md." 3 }
try { $c = Get-Content $CredPath -Raw | ConvertFrom-Json } catch { Die "secrets\mail.json is not valid JSON." 3 }
foreach ($f in 'email', 'app_password') { if (-not $c.$f) { Die "secrets\mail.json is missing '$f'." 3 } }
$pw       = ($c.app_password -replace '\s', '')             # app passwords are shown spaced; strip them
$smtpHost = if ($c.smtp_host) { $c.smtp_host } else { 'smtp.gmail.com' }
$smtpPort = if ($c.smtp_port) { [int]$c.smtp_port } else { 587 }
$imapHost = if ($c.imap_host) { $c.imap_host } else { 'imap.gmail.com' }

# ---- READ: raw IMAP over SSL (no external libraries) ----
if ($Read) {
    $tcp = $null; $ssl = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($imapHost, 993)
        $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false)
        $ssl.AuthenticateAsClient($imapHost)
        $sr = New-Object System.IO.StreamReader($ssl)
        $sw = New-Object System.IO.StreamWriter($ssl); $sw.NewLine = "`r`n"; $sw.AutoFlush = $true
        [void]$sr.ReadLine()  # server greeting
        $sw.WriteLine("a1 LOGIN `"$($c.email)`" `"$pw`"")
        $login = Read-ImapUntil $sr 'a1'
        if ($login -notmatch 'a1 OK') { throw ("IMAP login rejected (check the App Password / that IMAP is enabled).") }
        $sw.WriteLine('a2 STATUS INBOX (UNSEEN)')
        $st = Read-ImapUntil $sr 'a2'
        $unseen = if ($st -match 'UNSEEN (\d+)') { [int]$matches[1] } else { 0 }
        $sw.WriteLine('a3 SELECT INBOX'); [void](Read-ImapUntil $sr 'a3')
        $sw.WriteLine('a4 SEARCH UNSEEN')
        $se = Read-ImapUntil $sr 'a4'
        $ids = @()
        foreach ($ln in ($se -split "`n")) { if ($ln -match '^\* SEARCH (.+)$') { $ids = ($matches[1].Trim() -split '\s+') | Where-Object { $_ -match '^\d+$' } } }
        $ids = @($ids | Select-Object -Last $Count)
        Write-Host ("Unread in inbox: " + $unseen) -ForegroundColor Cyan
        $i = 0
        foreach ($id in $ids) {
            $sw.WriteLine("f$id FETCH $id (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            $resp = Read-ImapUntil $sr "f$id"
            $from = if ($resp -match '(?im)^From:\s*(.+)$') { $matches[1].Trim() } else { '' }
            $subj = if ($resp -match '(?im)^Subject:\s*(.+)$') { $matches[1].Trim() } else { '(no subject)' }
            $i++
            Write-Host ("[{0}] {1}" -f $i, $subj) -ForegroundColor White
            Write-Host ("     from: {0}" -f $from) -ForegroundColor DarkGray
        }
        if ($ids.Count -eq 0) { Write-Host "(nothing unread to list)" -ForegroundColor DarkGray }
        $sw.WriteLine('a9 LOGOUT'); [void](Read-ImapUntil $sr 'a9')
    }
    catch { Die ("IMAP read failed: " + $_.Exception.Message) 1 }
    finally { if ($ssl) { $ssl.Dispose() }; if ($tcp) { $tcp.Dispose() } }
    exit 0
}

# ---- COMPOSE: draft preview by default; send only with -Send -Confirm ----
if (-not $To -or -not $Subject) { Die 'Usage:  -Read  |  -To <addr> -Subject <s> -Body <b> [-Send -Confirm]' 2 }
if (-not $Send) {
    Write-Host "----- DRAFT (not sent) -----" -ForegroundColor Yellow
    Write-Host ("From:    " + $c.email); Write-Host ("To:      " + $To); Write-Host ("Subject: " + $Subject)
    Write-Host "---"; Write-Host $Body
    Write-Host "----- To actually send, re-run with:  -Send -Confirm -----" -ForegroundColor Yellow
    exit 0
}
if ($Send -and -not $Confirm) { Die "Refusing to send without -Confirm. Re-run with -Send -Confirm." 2 }
try {
    $smtp = New-Object System.Net.Mail.SmtpClient($smtpHost, $smtpPort)
    $smtp.EnableSsl = $true
    $smtp.Credentials = New-Object System.Net.NetworkCredential($c.email, $pw)
    $mail = New-Object System.Net.Mail.MailMessage($c.email, $To, $Subject, $Body)
    $smtp.Send($mail); $mail.Dispose(); $smtp.Dispose()
    Write-Host ("SENT to {0}: {1}" -f $To, $Subject) -ForegroundColor Green
}
catch { Die ("Send failed: " + $_.Exception.Message) 1 }
