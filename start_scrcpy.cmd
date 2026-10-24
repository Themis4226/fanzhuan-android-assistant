@echo off
setlocal
set "ADB=%~dp0tools\platform-tools\adb.exe"
set "SCRCPY_EXE=%~dp0tools\scrcpy-win64-v5.0.1\scrcpy.exe"
if not exist "%ADB%" set "ADB=%~dp0tools\scrcpy-win64-v5.0.1\adb.exe"
if not exist "%SCRCPY_EXE%" (
    echo scrcpy.exe was not found. See README.md.
    pause
    exit /b 2
)
if not exist "%ADB%" (
    echo Project adb.exe was not found. See README.md.
    pause
    exit /b 2
)
"%SCRCPY_EXE%" --select-usb --no-audio --no-clipboard-autosync --pause-on-exit=if-error %*
exit /b %errorlevel%
