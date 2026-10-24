@echo off
call "%~dp0run.cmd" doctor
set "PROBE_EXIT=%errorlevel%"
pause
exit /b %PROBE_EXIT%
