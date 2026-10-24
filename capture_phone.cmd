@echo off
call "%~dp0run.cmd" capture --open
set "PROBE_EXIT=%errorlevel%"
pause
exit /b %PROBE_EXIT%
