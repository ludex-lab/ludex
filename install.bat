@echo off
rem Ludex one-step installer (Windows). Double-click. Requires git + python (3.10+).
setlocal
set "DIR=%USERPROFILE%\ludex"
echo Installing Ludex into: %DIR%
if not exist "%DIR%\.git" (
  git clone https://github.com/ludex-lab/ludex "%DIR%"
) else (
  echo   ^(already there - updating^)
  git -C "%DIR%" pull --ff-only
)
cd /d "%DIR%"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
echo Installing dependencies...
.venv\Scripts\pip install -q -r requirements.txt
echo Starting Ludex (your browser will open)...
.venv\Scripts\python web\server.py
pause
