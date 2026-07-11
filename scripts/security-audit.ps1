<#
.SYNOPSIS
  Alfred security tool - read-only Windows security-posture audit of THIS PC. Reports antivirus,
  firewall, disk encryption, update recency, UAC, local admins, and listening ports. Changes nothing.
.DESCRIPTION
  Defensive diagnostics only - every check is read-only. Some checks (BitLocker, full Defender status)
  show more when run as Administrator, but the script never modifies system state.
.EXAMPLE  powershell -File scripts\security-audit.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'

function Line($label, $val, $flag) {
  $c = switch ($flag) { 'OK' {'Green'} 'WARN' {'Yellow'} 'BAD' {'Red'} default {'Gray'} }
  Write-Host ("  [{0,-4}] {1}: {2}" -f $flag, $label, $val) -ForegroundColor $c
}

Write-Host "=== Alfred security-posture audit (read-only) ===" -ForegroundColor Cyan
Write-Host ("Host: {0}   User: {1}   {2}" -f $env:COMPUTERNAME, $env:USERNAME, (Get-Date -Format o))

Write-Host "`n-- Antivirus (Defender) --"
try {
  $d = Get-MpComputerStatus -ErrorAction Stop
  Line 'Real-time protection' $d.RealTimeProtectionEnabled $(if ($d.RealTimeProtectionEnabled) {'OK'} else {'WARN'})
  Line 'Antivirus enabled'    $d.AntivirusEnabled          $(if ($d.AntivirusEnabled) {'OK'} else {'WARN'})
  $age = $d.AntivirusSignatureAge
  Line 'Signature age (days)' $age $(if ($age -le 3) {'OK'} elseif ($age -le 14) {'WARN'} else {'BAD'})
} catch { Line 'Defender' "not available" 'WARN' }

Write-Host "`n-- Firewall --"
try {
  Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object {
    Line ("Profile " + $_.Name) $_.Enabled $(if ($_.Enabled) {'OK'} else {'BAD'})
  }
} catch { Line 'Firewall' "not available" 'WARN' }

Write-Host "`n-- Disk encryption (BitLocker) --"
try {
  Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
    Line ("Volume " + $_.MountPoint) $_.ProtectionStatus $(if ($_.ProtectionStatus -eq 'On') {'OK'} else {'WARN'})
  }
} catch { Line 'BitLocker' "needs admin or not present" 'WARN' }

Write-Host "`n-- Updates --"
try {
  $hf = Get-HotFix -ErrorAction Stop | Sort-Object InstalledOn -Descending | Select-Object -First 1
  $days = if ($hf.InstalledOn) { (New-TimeSpan -Start $hf.InstalledOn -End (Get-Date)).Days } else { $null }
  Line 'Last hotfix' ("{0} ({1} days ago)" -f $hf.HotFixID, $days) $(if ($days -ne $null -and $days -le 45) {'OK'} elseif ($days -le 90) {'WARN'} else {'BAD'})
} catch { Line 'Updates' "not available" 'WARN' }

Write-Host "`n-- UAC --"
try {
  $uac = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction Stop).EnableLUA
  Line 'UAC (EnableLUA)' $uac $(if ($uac -eq 1) {'OK'} else {'BAD'})
} catch { Line 'UAC' "unknown" 'WARN' }

Write-Host "`n-- Local administrators --"
try {
  $admins = Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop
  Line 'Admin accounts' ($admins.Name -join ', ') $(if ($admins.Count -le 3) {'OK'} else {'WARN'})
} catch { Line 'Admins' "not available" 'WARN' }

Write-Host "`n-- Listening TCP ports (top 15 by port) --"
try {
  Get-NetTCPConnection -State Listen -ErrorAction Stop | Sort-Object LocalPort -Unique |
    Select-Object -First 15 | ForEach-Object {
      $p = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
      Write-Host ("  port {0,-6} <- {1}" -f $_.LocalPort, $p) -ForegroundColor Gray
    }
} catch { Line 'Ports' "not available" 'WARN' }

Write-Host "`n[audit] read-only; nothing was changed. Review any WARN/BAD items above." -ForegroundColor DarkGray
