@echo off
cd /d "%~dp0"
set PORT=8080
set PORT_STRICT=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
"C:\Users\Administrateur\AppData\Local\Programs\Python\Python312\python.exe" server.py
pause
