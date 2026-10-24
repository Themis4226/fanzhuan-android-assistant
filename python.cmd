@echo off
setlocal
set "PYTHONUTF8=1"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" -X utf8 %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" %*
) else (
    py -3 %*
)
exit /b %errorlevel%
