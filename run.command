#!/bin/sh
# Double-click to start the Ludex local app (macOS/Linux).
# Assumes you've run the one-time setup (it creates .venv). Then your browser opens.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. Run the one-time setup first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r _ 2>/dev/null
  exit 1
fi
exec .venv/bin/python web/server.py "$@"
