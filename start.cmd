@echo off
chcp 65001 >nul
call "%~dp0python.cmd" "%~dp0launcher.py" %*
if errorlevel 1 pause
