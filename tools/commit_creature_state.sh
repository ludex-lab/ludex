#!/usr/bin/env bash
#
# commit_creature_state.sh — daily batch commit of creature-state data.
#
# Creature habitats (creatures/) mutate continuously: heartbeat reflects,
# snapshots, spans, SELF.md updates. Leaving them uncommitted risks losing
# research data to a working-tree accident and makes provenance fuzzy.
# Cadence decision 2026-06-11 (JJ + Cody): a daily batch commit, manually
# triggerable, rather than per-heartbeat auto-commits.
#
# Usage:   bash tools/commit_creature_state.sh
# Cron:    safe to wire into launchd/cron later; commits only, never pushes.
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git add creatures/

if git diff --cached --quiet -- creatures/; then
    echo "creature state clean — nothing to commit"
    exit 0
fi

SUMMARY=$(git diff --cached --stat -- creatures/ | tail -1)
git commit -m "data: creature-state daily batch $(date '+%Y-%m-%d')

${SUMMARY}" -- creatures/
echo "committed: $(git log --oneline -1)"
