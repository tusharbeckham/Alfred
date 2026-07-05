---
name: pc-management
description: Safe Windows PC management — inspecting system state, processes, disk, and services via PowerShell. Use for PC housekeeping and diagnostics. Read-first, heavily safety-gated.
---

# PC Management (Windows)

## Prime rule: READ-FIRST, ASK-BEFORE-CHANGE
Inspection is safe and encouraged. Any *change* to the system is safety-gated — ask the
Owner first (see steering/safety.md). This skill is diagnostics-first.

## Safe read-only inspections
```powershell
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10   # top CPU
Get-Process | Sort-Object WS  -Descending | Select-Object -First 10   # top memory
Get-CimInstance Win32_LogicalDisk | Select DeviceID, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}
Get-Service | Where-Object Status -eq 'Running'                       # running services
Get-ComputerInfo | Select CsName, OsName, OsVersion, CsTotalPhysicalMemory
Get-NetIPConfiguration                                                # network (read)
Get-WinEvent -LogName System -MaxEvents 20                            # recent events
```

## MUST ask the Owner before (gated)
- Killing/stopping processes or services the Owner did not name.
- Editing the registry, drivers, scheduled tasks, startup items.
- Changing network/firewall config, power settings, or user accounts.
- Deleting files, clearing caches broadly, uninstalling software.
- Anything under `C:\Windows\`, `System32`, or Program Files.

## When a change IS approved
- Show the exact command and expected effect first.
- Prefer reversible actions; note how to undo. Capture before/after state.
- Never run destructive one-liners (`Remove-Item -Recurse`, `format`, `reg delete`) without
  explicit, specific approval.

## Reporting
Return findings as a compact table. Lead with the answer (e.g., "Disk C: 42GB free of
512GB; top memory user is chrome at 3.1GB").
