#!/bin/bash
# schedule_updates.command — double-click once to run the bot's updates
# automatically (daily data, pre-game line saves, weekly refit).
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
.venv/bin/python src/schedule_install.py "$@"
echo ""
read -r -p "Press Return to close."
