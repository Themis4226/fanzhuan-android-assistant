@echo off
chcp 65001 >nul
if exist "%~dp0翻转外语助手.exe" (
    start "" "%~dp0翻转外语助手.exe"
    exit /b 0
)
call "%~dp0python.cmd" "%~dp0launcher.py" %*
if errorlevel 1 pause
