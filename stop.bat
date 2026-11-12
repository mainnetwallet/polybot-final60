@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
"%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul
exit /b 0
