#!/usr/bin/env bash
# Ludex launcher (macOS · Linux) — set up the Python env, report which brain
# CLIs you have, and start the local web app (Forge · Chat · Village · Timeline).
#
# The brain is your OWN LLM CLI (claude / codex / gemini / ollama / …); Ludex
# just orchestrates it, so this launcher stays tiny. It creates a self-contained
# .venv on first run and never touches your system Python. Pass server flags
# through, e.g.  ./start.sh --port 7870 --no-browser
set -euo pipefail
cd "$(dirname "$0")"

# 1. Python — prefer the project venv; create it on first run.
if [ ! -x ".venv/bin/python" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ Python 3 not found. Install Python 3.10+ (python.org) and re-run." >&2
    exit 1
  fi
  echo "• Creating .venv (first run)…"
  python3 -m venv .venv
fi
PY=".venv/bin/python"

# 2. Dependencies — install only if a core import is missing (fast path skips pip).
if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "• Installing dependencies (first run)…"
  "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  "$PY" -m pip install -q -r requirements.txt
fi

# 3. Brain-CLI report — the brain is user-owned, so tell them what's detected.
echo "• Brain CLIs detected:"
found=0
for cli in claude codex grok gemini agy ollama; do
  if command -v "$cli" >/dev/null 2>&1; then echo "    ✓ $cli"; found=1; fi
done
if [ "$found" = 0 ]; then
  echo "    (none — install at least one LLM CLI, e.g. 'claude' or 'ollama',"
  echo "     so a creature has a brain. Ludex will still open; creatures need a CLI.)"
fi

# 4. Launch. The server opens your browser and prints the URL itself.
echo "• Starting Ludex…"
exec "$PY" web/server.py "$@"
