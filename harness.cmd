@echo off
REM Alfred harness - the single policy-gated entrypoint for automating this machine.
REM
REM   harness verify
REM   harness list --caller owner
REM   harness run status
REM   harness run git-status --param path=C:\Alfred
REM   harness run backup --approve
REM
REM Every call is checked against the SIGNED policy in policy\harness-policy.json and
REM appended to memory\harness-audit.jsonl. Deny by default.
python "%~dp0scripts\harness.py" %*
