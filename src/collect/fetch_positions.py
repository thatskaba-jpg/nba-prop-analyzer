"""
fetch_positions.py — fill real player POSITIONS from balldontlie (free field).

The positional-defense feature (positional_defense.py) works off a Guard/Wing/Big
group per player. Without this, it INFERS the group from each player's stat line
(a decent approximation). This script upgrades that to the REAL position by
pulling balldontlie's free `position` field for every player already in our DB,
storing it in a `player_positions` table that positional_defense.py prefers.

Run this when the stats download is NOT running (so you're not fighting the
5 req/min trial limit):

    python3 src/collect/fetch_positions.py

It only looks up players we actually have game data for, pages politely, and is
safe to re-run (INSERT OR REPLACE). If the trial rate-limits it (HTTP 429),
just run it again later — it skips players already stored.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection  # noqa: E402
from balldontlie_client import iter_pages, BallDontLieError  # noqa: E402


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_positions (
            player_id INTEGER PRIMARY KEY,
            name      TEXT,
            position  TEXT
        )
        """)
    conn.commit()


def fetch_positions():
    conn = get_connection()
    _ensure_table(conn)

    already = {r["player_id"] for r in conn.execute(
        "SELECT player_id FROM player_positions WHERE position IS NOT NULL "
        "AND position != ''")}
    print(f"Already have positions for {len(already):,} players. Fetching the rest...")

    stored = 0
    try:
        # balldontlie /players returns position per player; page through all.
        for rows, _cursor in iter_pages("players", {}, start_cursor=None):
            conn.execute("BEGIN")
            for p in rows:
                pid = p.get("id")
                if pid is None:
                    continue
                pos = (p.get("position") or "").strip()
                name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
                conn.execute(
                    "INSERT OR REPLACE INTO player_positions "
                    "(player_id, name, position) VALUES (?,?,?)",
                    (int(pid), name, pos))
                stored += 1
            conn.commit()
            print(f"  ...stored {stored} player positions so far")
    except BallDontLieError as e:
        conn.close()
        print(f"API error: {e}")
        print("Progress saved — re-run later to continue (already-stored "
              "players are skipped).")
        return
    except KeyboardInterrupt:
        conn.close()
        print("\nStopped. Progress saved — re-run later.")
        return

    # How many of OUR players now have a position?
    have = conn.execute(
        "SELECT COUNT(*) FROM player_positions WHERE position != ''").fetchone()[0]
    ours = conn.execute(
        "SELECT COUNT(DISTINCT player_id) FROM player_game_logs").fetchone()[0]
    conn.close()
    print(f"\nDone. {have:,} players have a real position. "
          f"We have game data for {ours:,} players; any not covered fall back "
          f"to inferred Guard/Wing/Big automatically.")


if __name__ == "__main__":
    fetch_positions()
