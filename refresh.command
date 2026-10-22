#!/bin/bash
# refresh.command — the ONE way to refresh your bot.
#
# What it does: pulls new games, fills in opponents/home-away, refreshes
# injuries, and grades your logged predictions — all in order, every time.
# This is the standard refresh; you never have to run the pieces by hand.
#
# Two ways to use it:
#   1. Double-click this file in Finder.
#   2. In Terminal:  ./refresh.command     (from the project folder)
#
# It figures out its own location, so it works no matter where you run it from.

cd "$(dirname "$0")" || exit 1

if [ ! -d ".venv" ]; then
  echo "Can't find the .venv folder. Are you in the sports-betting-bot folder?"
  exit 1
fi

source .venv/bin/activate
echo "Refreshing your NBA bot — this keeps everything current."
echo ""
python3 src/collect/daily_update.py

echo ""
echo "All set. You can close this window."
