@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ms_rewards\scheduler\estado.ps1"
echo.
pause
