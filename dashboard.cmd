@echo off
REM Alfred dashboard - the local, read-only control surface.
REM
REM   dashboard                 open the UI on a free port
REM   dashboard --port 7373     pin the port
REM   dashboard --no-browser    just print the URL
REM   dashboard --check         dump the whole snapshot as JSON and exit
REM
REM Binds 127.0.0.1 ONLY and mints a fresh session token each start - the URL it
REM prints contains that token. It observes and never executes: actions still go
REM through harness.cmd so every side effect stays under the signed policy.
python "%~dp0scripts\dashboard.py" %*
