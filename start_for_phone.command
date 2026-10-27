#!/bin/bash
# start_for_phone.command — start the dashboard AND keep this Mac awake while it
# runs, so your phone (via Tailscale) can reach it. Double-click to start;
# close the window or press Control-C to stop (the Mac can sleep again).
#
# caffeinate -d -i -m -s : stop display, idle, disk and system sleep (system
# sleep only holds while on power). It lasts exactly as long as the dashboard.
HERE="$(cd "$(dirname "$0")" && pwd)"
echo "Keeping this Mac awake while the dashboard runs."
echo "Phone: turn Tailscale on, then open your Tailscale link."
echo ""
exec caffeinate -d -i -m -s "$HERE/start_dashboard.command"
