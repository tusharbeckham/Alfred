@echo off
REM Alfred console - the interactive terminal surface for the whole system.
REM
REM   alfred                      interactive console
REM   alfred status               one-shot: probe every subsystem
REM   alfred run feature-gated    execute a graph with live motion
REM   alfred ask "..."            ask the local model, with memory injected
REM   alfred graph deploy-gated   draw a graph and its gate edges
REM   alfred recall "..."         hybrid recall from the memory graph
REM   alfred lms up               start LM Studio's server and load a model
REM
REM Every command is also scriptable: the console runs one-shot when given args.
REM Chcp 65001 so box-drawing glyphs render instead of raising UnicodeEncodeError.
chcp 65001 >nul 2>&1
python "%~dp0scripts\console.py" %*
