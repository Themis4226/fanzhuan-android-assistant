@echo off
call "%~dp0assist.cmd" --demo --keep-image
set "OCR_EXIT=%errorlevel%"
pause
exit /b %OCR_EXIT%
