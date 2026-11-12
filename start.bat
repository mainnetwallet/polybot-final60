@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 (
    echo.
    echo Startup failed. Review the errors above and the files in the logs folder.
    pause
    exit /b 1
)
exit /b 0
