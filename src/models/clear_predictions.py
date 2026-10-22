"""
clear_predictions.py — reset the prediction track record.

Deletes logged predictions so the Track Record tab starts clean. Use it to wipe
early test entries, or to start a fresh tracking period.

    python3 src/models/clear_predictions.py            # clear ALL predictions
    python3 src/models/clear_predictions.py smoketest  # clear only one model's

Only touches the `predictions` table — your game data (player_game_logs) is
never affected.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402


def clear(model_name=None):
    conn = get_connection()
    # Guard: if the table doesn't exist yet, nothing to do.
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not exists:
        print("No predictions table yet — nothing to clear.")
        conn.close()
        return
    before = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    if model_name:
        conn.execute("DELETE FROM predictions WHERE model_name = ?", (model_name,))
    else:
        conn.execute("DELETE FROM predictions")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    conn.close()
    scope = f"model '{model_name}'" if model_name else "all models"
    print(f"Cleared {before - after} prediction(s) ({scope}). "
          f"Track record now holds {after}.")


if __name__ == "__main__":
    clear(sys.argv[1] if len(sys.argv) > 1 else None)
