#!/bin/bash
# start_dashboard.command — start the NBA bot dashboard AND auto-open it in your
# browser. Double-click in Finder, or run ./start_dashboard.command in Terminal.

# Always run from THIS file's own folder, using an absolute path.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || { echo "Could not enter $HERE"; sleep 5; exit 1; }

echo "Project folder: $HERE"

# Find the venv's streamlit directly (no 'source' needed — more reliable).
STREAMLIT="$HERE/.venv/bin/streamlit"

if [ ! -x "$STREAMLIT" ]; then
  echo ""
  echo "ERROR: can't find Streamlit at:"
  echo "  $STREAMLIT"
  echo ""
  echo "That usually means the virtual environment isn't set up here."
  echo "Fix: open Terminal and run these, then try again:"
  echo "    cd ~/sports-betting-bot"
  echo "    python3 -m venv .venv"
  echo "    source .venv/bin/activate"
  echo "    pip install -r requirements.txt"
  echo ""
  echo "(This window stays open so you can read the message.)"
  read -r -p "Press Return to close."
  exit 1
fi

PORT=8501
URL="http://localhost:$PORT"

echo "Starting the NBA bot dashboard..."
echo "Your browser will open automatically once it's ready."
echo "Keep this window open while you use it. Press Control-C here to stop."
echo ""

# Open the browser as soon as the server is actually listening on the port.
# Runs in the background, waits for the port, then opens the default browser.
( for i in $(seq 1 30); do
    # nc returns success when something is listening on the port.
    if nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
      open "$URL"
      break
    fi
    sleep 1
  done ) &

# Run Streamlit in the foreground. --server.headless true stops Streamlit from
# ALSO trying to open a tab (we handle that above), avoiding a double tab.
"$STREAMLIT" run "$HERE/dashboard/app.py" \
  --server.port "$PORT" \
  --server.headless true
STATUS=$?

if [ $STATUS -ne 0 ]; then
  echo ""
  echo "The dashboard stopped with an error (code $STATUS) — see above."
  read -r -p "Press Return to close."
fi
