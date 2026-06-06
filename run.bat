@echo off
rem Double-click to start the Ludex local app (Windows). Your browser opens.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Run the one-time setup first:
  echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python" web\server.py %*
