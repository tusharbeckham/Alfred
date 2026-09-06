<#
.SYNOPSIS
  Ultron - Unified launcher for Alfred & Ultron-CLI (B.AI enabled).
#>
$usePython = $false
if ($args.Count -gt 0 -and $args[0] -eq "doctor") {
    $usePython = $true
}
foreach ($a in $args) {
    if ($a -eq "--agent" -or $a -eq "-a") {
        $usePython = $true
        break
    }
}

if ($usePython) {
    python (Join-Path $PSScriptRoot 'scripts\ultron.py') @args
} else {
    node "c:\projects\ultron-cli\bin\ultron.mjs" @args
}
exit $LASTEXITCODE
