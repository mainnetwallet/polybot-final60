@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
    echo.
    echo Setup failed. Review the errors above, then run setup.bat again.
    pause
    exit /b 1
)
echo.
pause
exit /b 0
