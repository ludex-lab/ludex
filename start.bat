@echo off
REM Ludex launcher (Windows) — set up the Python env, report which brain CLIs
REM you have, and start the local web app (Forge / Chat / Village / Timeline).
REM
REM The brain is your OWN LLM CLI (claude / codex / gemini / ollama / ...);
REM Ludex just orchestrates it. A self-contained .venv is created on first run.
REM Pass server flags through, e.g.  start.bat --port 7870 --no-browser
setlocal
cd /d "%~dp0"

REM 1. Python — prefer the project venv; create it on first run.
if exist ".venv\Scripts\python.exe" goto haveenv
where python >nul 2>nul || (
  echo Python 3 not found. Install Python 3.10+ from python.org and re-run.
  pause
  exit /b 1
)
echo - Creating .venv ^(first run^)...
python -m venv .venv
:haveenv
set "PY=.venv\Scripts\python.exe"

REM 2. Dependencies — install only if a core import is missing.
"%PY%" -c "import fastapi, uvicorn" >nul 2>nul || (
  echo - Installing dependencies ^(first run^)...
  "%PY%" -m pip install -q -r requirements.txt
)

REM 3. Brain-CLI report — the brain is user-owned, so show what's detected.
echo - Brain CLIs detected:
for %%C in (claude codex grok gemini agy ollama) do where %%C >nul 2>nul && echo       [x] %%C

REM 4. Launch. The server opens your browser and prints the URL itself.
echo - Starting Ludex...
"%PY%" web\server.py %*
