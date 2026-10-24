@echo off
call "%~dp0start_scrcpy.cmd" --no-control --no-power-on %*
exit /b %errorlevel%
