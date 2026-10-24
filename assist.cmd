@echo off
setlocal
set "PYTHONUTF8=1"
call "%~dp0python.cmd" "%~dp0assistant_step.py" %*
exit /b %errorlevel%
