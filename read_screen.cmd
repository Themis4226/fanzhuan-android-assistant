@echo off
call "%~dp0assist.cmd" --live %*
set "OCR_EXIT=%errorlevel%"
pause
exit /b %OCR_EXIT%
