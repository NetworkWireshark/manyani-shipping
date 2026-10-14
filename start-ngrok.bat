@echo off
cd /d "%~dp0"
if not exist "C:\ngrok\ngrok.exe" (
  echo ngrok.exe missing at C:\ngrok\ngrok.exe
  pause
  exit /b 1
)
"C:\ngrok\ngrok.exe" http 8080 --url=https://erica-bignoniaceous-tyson.ngrok-free.dev
pause
