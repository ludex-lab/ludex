@echo off
rem Double-click to start the Ludex local app (Windows). Your browser opens.
cd /d "%~dp0"
rem Stay current automatically: pull the latest + sync deps only when something changed,
rem so you never run git by hand. Skips quietly when offline or this isn't a git install.
setlocal enabledelayedexpansion
if exist ".git" (
  for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "BEFORE=%%i"
  git pull --ff-only >nul 2>&1
  for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "AFTER=%%i"
  if not "!BEFORE!"=="!AFTER!" (
    echo Updated Ludex to the latest - one moment...
    if exist ".venv\Scripts\pip.exe" ".venv\Scripts\pip" install -q -r requirements.txt >nul 2>&1
  )
)
endlocal
if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Run the one-time setup first:
  echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python" web\server.py %*
