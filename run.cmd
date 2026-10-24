@echo off
setlocal
set "PYTHONUTF8=1"
call "%~dp0python.cmd" "%~dp0phone_capture.py" %*
exit /b %errorlevel%
