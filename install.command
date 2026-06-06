#!/bin/sh
# Ludex one-step installer (macOS / Linux).
# Double-click it, or run:  curl -fsSL https://raw.githubusercontent.com/ludex-lab/ludex/main/install.command | sh
# Requires: git + python3 (3.10+).
set -e
DIR="${LUDEX_DIR:-$HOME/ludex}"
echo "→ Installing Ludex into: $DIR"
if [ ! -d "$DIR/.git" ]; then
  git clone https://github.com/ludex-lab/ludex "$DIR"
else
  echo "  (already there — updating)"; git -C "$DIR" pull --ff-only || true
fi
cd "$DIR"
[ -x .venv/bin/python ] || python3 -m venv .venv
echo "→ Installing dependencies…"
.venv/bin/pip install -q -r requirements.txt
echo "→ Starting Ludex (your browser will open)…"
exec .venv/bin/python web/server.py
