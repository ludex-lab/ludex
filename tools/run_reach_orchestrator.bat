@echo off
REM ============================================================
REM Wrapper for ludex.reach.reach_orchestrator on Windows.
REM
REM R4.P v1 (2026-04-25) found that bash-style assignment of
REM CLAUDE_CODE_GIT_BASH_PATH=D:\Git\bin\bash.exe strips backslashes
REM by the time claude.cmd reads the env var, producing
REM "D:Gitbinbash.exe" which Claude Code rejects. cmd.exe's
REM `set "VAR=..."` preserves backslashes faithfully, which is
REM why this wrapper exists.
REM
REM Usage:
REM   tools\run_reach_orchestrator.bat ^
REM     --repo-root D:\projects\ludus-ex-machina ^
REM     --session-id reach_2026-04-25_hearth_primo_p_smoke_002 ^
REM     --creature Hearth ^
REM     --machine-id 92520f1d-ea8b-4b7d-99dc-b50ad5e817d0 ^
REM     --machine-alias win-nautilus-001 ^
REM     --habitat D:\projects\ludex\creatures\Hearth ^
REM     --poll-interval 5 --idle-grace 1200
REM
REM PowerShell alternative:
REM   $env:CLAUDE_CODE_GIT_BASH_PATH = "D:\Git\bin\bash.exe"
REM   $env:PYTHONIOENCODING = "utf-8"
REM   python -m ludex.reach.reach_orchestrator --repo-root ... ...
REM ============================================================

setlocal

REM Required env for claude_cli (Hearth) on Windows.
set "CLAUDE_CODE_GIT_BASH_PATH=D:\Git\bin\bash.exe"

REM Force UTF-8 stdout so Korean / em-dash content does not crash
REM cp949 default on creature reflections.
set "PYTHONIOENCODING=utf-8"

REM Repo root for Ludex package.
set "REPO_ROOT=D:\projects\ludex"
cd /d "%REPO_ROOT%" || exit /b 1

python -m ludex.reach.reach_orchestrator %*
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
