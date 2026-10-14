@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-tracking.ps1"
if errorlevel 1 echo Restart failed. Right-click this file and choose Run as administrator.
pause
