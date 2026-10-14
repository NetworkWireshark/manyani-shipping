@echo off
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PORT=80
set PORT_STRICT=1
"C:\Users\Administrateur\AppData\Local\Programs\Python\Python312\python.exe" server.py 1>>"%~dp0data\server.log" 2>&1
