#!/bin/sh
# Links this clone into the places Claude Code and the desk scripts expect.
# Data files (desk.json, cards.json, caches) stay in ~/orbit-scratch/desk and never enter the repo.
set -e
R="$(cd "$(dirname "$0")" && pwd)"
link() { mkdir -p "$(dirname "$2")"; [ -e "$2" ] && [ ! -L "$2" ] && mv "$2" "$2.pre-repo"; ln -sfn "$1" "$2"; }
link "$R/mod" "$HOME/.claude/mods/desk"
link "$R/skill" "$HOME/.claude/skills/desk"
for f in "$R"/scripts/*.py; do link "$f" "$HOME/orbit-scratch/desk/$(basename "$f")"; done
# The repo is public: the hooks refuse a commit or push that carries private data (.githooks/scan.py).
git -C "$R" config core.hooksPath .githooks
echo "linked. add to ~/.claude/settings.json env: CLAUDE_CODE_PLUGIN_DIRS=$HOME/.claude/mods/desk"
