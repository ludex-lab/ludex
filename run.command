#!/bin/sh
# Double-click to start the Ludex local app (macOS/Linux).
# Assumes you've run the one-time setup (it creates .venv). Then your browser opens.
cd "$(dirname "$0")"
# Stay current automatically: pull the latest + sync dependencies only when something
# changed — so you never run git by hand. Skips silently when you're offline or this
# isn't a git install; the app still opens either way.
if git rev-parse --git-dir >/dev/null 2>&1; then
  _before=$(git rev-parse HEAD 2>/dev/null)
  git pull --ff-only >/dev/null 2>&1 || true
  if [ "$_before" != "$(git rev-parse HEAD 2>/dev/null)" ]; then
    echo "✓ Updated Ludex to the latest — one moment…"
    [ -x .venv/bin/pip ] && .venv/bin/pip install -q -r requirements.txt >/dev/null 2>&1 || true
  fi
fi
if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. Run the one-time setup first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r _ 2>/dev/null
  exit 1
fi
exec .venv/bin/python web/server.py "$@"
