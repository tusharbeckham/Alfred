@echo off
REM Ultron - Alfred's local CLI launcher (mirrors `kiro-cli` usage, but free/offline).
REM   ultron agents
REM   ultron run --agent alfred-qa "draft a test plan"
REM   ultron chat --agent alfred-coder
python "%~dp0scripts\ultron.py" %*
