@echo off
REM Ultron - Unified launcher for Alfred & Ultron-CLI (B.AI enabled)
setlocal enabledelayedexpansion

set "USE_PYTHON="
if "%~1"=="doctor" set "USE_PYTHON=1"
for %%A in (%*) do (
  if "%%~A"=="--agent" set "USE_PYTHON=1"
  if "%%~A"=="-a" set "USE_PYTHON=1"
)

if defined USE_PYTHON (
  python "%~dp0scripts\ultron.py" %*
) else (
  node "c:\projects\ultron-cli\bin\ultron.mjs" %*
)
